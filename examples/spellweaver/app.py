"""Spellweaver — semantic dispatch as gameplay.

The player types free-form incantations; grimoire semantically matches
the prose to a spell from the indexed grimoire. Top hit = cast spell;
runner-up = "what you almost cast"; distance > FIZZLE_THRESHOLD = the
incantation fizzles. The goblin warlock retaliates each turn.

Showcases entry_vec as a runtime dispatcher: the same query surface
that powers a search box can act as a fuzzy lookup of named actions.
"""

from __future__ import annotations

import argparse
import random
from dataclasses import dataclass, field
from pathlib import Path

from grimoire.data.entry import Entry
from grimoire.embed import FastembedEmbedder
from grimoire.errors import GrimoireNotFound
from grimoire.grimoire import Grimoire
from grimoire.mount import Mount, create

MOUNT = Mount(path=Path(__file__).parent / ".grimoire")

FIZZLE_THRESHOLD = 0.95

SPELLS: list[dict] = [
    {
        "name": "Fireball",
        "kind": "damage",
        "magnitude": 25,
        "cost": 25,
        "blurb": "A roaring sphere of flame that explodes on contact.",
        "imagery": (
            "fire flame heat burn scorch ignite blaze inferno immolation "
            "combustion pyre hellfire conflagration"
        ),
    },
    {
        "name": "Ice Shard",
        "kind": "damage",
        "magnitude": 15,
        "cost": 12,
        "blurb": "A jagged splinter of ice that punctures with a freezing sting.",
        "imagery": "ice frost cold freeze frozen chill winter shard glacial hoar",
    },
    {
        "name": "Lightning Bolt",
        "kind": "damage",
        "magnitude": 22,
        "cost": 20,
        "blurb": "A crack of forked lightning that arcs from sky to target.",
        "imagery": (
            "lightning thunder electric spark bolt storm shock voltage arc current sky"
        ),
    },
    {
        "name": "Magic Missile",
        "kind": "damage",
        "magnitude": 8,
        "cost": 5,
        "blurb": "Three darts of pure arcane force that always strike true.",
        "imagery": "arcane dart missile force projectile unerring true straight",
    },
    {
        "name": "Heal",
        "kind": "heal",
        "magnitude": 25,
        "cost": 20,
        "blurb": "Restorative light that knits flesh and mends wounds.",
        "imagery": (
            "heal restore mend cure recovery health vitality wellness light "
            "salve regenerate body wound"
        ),
    },
    {
        "name": "Greater Heal",
        "kind": "heal",
        "magnitude": 50,
        "cost": 40,
        "blurb": "A radiant surge that returns the caster to near-full vitality.",
        "imagery": "greater heal full restoration radiant divine renewal rebirth grand",
    },
    {
        "name": "Sleep",
        "kind": "stun",
        "magnitude": 2,
        "cost": 18,
        "blurb": "A whispered lullaby that drops the foe into unconsciousness.",
        "imagery": "sleep slumber unconscious dream drowse lullaby rest doze whisper mind",
    },
    {
        "name": "Detect Magic",
        "kind": "reveal",
        "magnitude": 0,
        "cost": 3,
        "blurb": "A faint shimmer that reveals the magical signature of nearby beings.",
        "imagery": (
            "detect reveal see scan inspect identify divine sense perceive observe "
            "uncover analyze appraise"
        ),
    },
    {
        "name": "Drain Life",
        "kind": "drain",
        "magnitude": 12,
        "cost": 18,
        "blurb": "Tendrils of shadow that siphon life from the foe to the caster.",
        "imagery": (
            "drain siphon steal vampiric absorb life shadow leech sap consume "
            "nourish blood"
        ),
    },
    {
        "name": "Disintegrate",
        "kind": "damage",
        "magnitude": 40,
        "cost": 45,
        "blurb": "A beam of unmaking that reduces matter to dust.",
        "imagery": (
            "disintegrate annihilate vaporize obliterate erase unmake dissolve dust "
            "destroy utterly"
        ),
    },
    {
        "name": "Time Stop",
        "kind": "skip",
        "magnitude": 2,
        "cost": 50,
        "blurb": "The world pauses for the breadth of an inhalation.",
        "imagery": (
            "time stop pause halt freeze still moment instant chronomancy hold world"
        ),
    },
    {
        "name": "Mending",
        "kind": "heal",
        "magnitude": 8,
        "cost": 5,
        "blurb": "A gentle pulse that stitches small cuts and bruises.",
        "imagery": "mending small heal patch stitch minor scrape bruise cut gentle pulse",
    },
]


DEMO_INCANTATIONS = [
    "I summon roaring flames to engulf my enemy",
    "let me see what magic this creature wields",
    "I siphon the life from this foe to nourish my own",
    "hurl a bolt of lightning from the heavens",
    "restore my wounded body to full vigor",
    "let the world hold still for a heartbeat",
    "annihilate it utterly, leave only dust",
]


@dataclass
class Combatant:
    name: str
    hp: int
    max_hp: int
    mana: int = 0
    max_mana: int = 0
    stunned_turns: int = 0
    revealed: bool = False
    log: list[str] = field(default_factory=list)


# ----------------------------------------------------------------------
# Grimoire wiring
# ----------------------------------------------------------------------


def needs_seed(db_path: Path) -> bool:
    if not db_path.exists() or db_path.stat().st_size == 0:
        return True
    try:
        peek = Grimoire.peek(db_path)
    except GrimoireNotFound:
        return True
    return peek.entry_count == 0


def seed(g: Grimoire) -> None:
    entries = [Entry(uniq_id=None, data=s) for s in SPELLS]
    inserted = g.add(entries)
    for created, s in zip(inserted, SPELLS, strict=True):
        text = f"{s['name']}. {s['blurb']} Imagery: {s['imagery']}."
        g.index(created.uniq_id, search=text)


# ----------------------------------------------------------------------
# Combat
# ----------------------------------------------------------------------


def apply_effect(spell: dict, player: Combatant, goblin: Combatant) -> None:
    kind = spell["kind"]
    mag = spell["magnitude"]
    name = spell["name"]
    if kind == "damage":
        actual = min(mag, goblin.hp)
        goblin.hp -= actual
        print(f"    > {name} strikes the goblin for {actual} damage.")
    elif kind == "heal":
        actual = min(mag, player.max_hp - player.hp)
        player.hp += actual
        print(f"    > You recover {actual} HP.")
    elif kind == "drain":
        dealt = min(mag, goblin.hp)
        goblin.hp -= dealt
        gained = min(mag, player.max_hp - player.hp)
        player.hp += gained
        print(f"    > Drained {dealt} from the goblin; recovered {gained} HP.")
    elif kind == "stun":
        goblin.stunned_turns = max(goblin.stunned_turns, mag)
        print(f"    > The goblin falls unconscious for {mag} turn(s).")
    elif kind == "skip":
        goblin.stunned_turns = max(goblin.stunned_turns, mag)
        print(f"    > Time stutters; the goblin loses {mag} turn(s).")
    elif kind == "reveal":
        player.revealed = True
        print(f"    > [{goblin.name}: {goblin.hp}/{goblin.max_hp} HP]")


def cast(
    g: Grimoire, prose: str, player: Combatant, goblin: Combatant
) -> bool:
    """Run one casting attempt. Returns True iff a spell was actually cast."""
    entries, hits = g.search(prose, limit=2)
    if not entries:
        print("  The words echo and die. (no spells indexed)")
        return False

    top_e, top_h = entries[0], hits[0]
    runner = (entries[1], hits[1]) if len(entries) > 1 else None

    if top_h.distance > FIZZLE_THRESHOLD:
        nearest = top_e.data["name"]
        print(
            f"  ✗ The incantation fizzles "
            f"(closest match: {nearest}, d={top_h.distance:.3f} > {FIZZLE_THRESHOLD})"
        )
        return False

    spell = top_e.data
    if spell["cost"] > player.mana:
        print(
            f"  ✗ Not enough mana for {spell['name']} "
            f"(needs {spell['cost']}, you have {player.mana})"
        )
        return False

    print(
        f"  ✓ {spell['name']}  (d={top_h.distance:.3f}, cost {spell['cost']} mana)"
    )
    if runner is not None:
        r_e, r_h = runner
        print(
            f"    … almost cast {r_e.data['name']}  (d={r_h.distance:.3f})"
        )
    player.mana -= spell["cost"]
    apply_effect(spell, player, goblin)
    return True


def goblin_attack(player: Combatant, goblin: Combatant, rng: random.Random) -> None:
    if goblin.stunned_turns > 0:
        goblin.stunned_turns -= 1
        print(f"  ({goblin.name} is incapacitated; skipping their turn.)")
        return
    dmg = rng.randint(6, 12)
    actual = min(dmg, player.hp)
    player.hp -= actual
    print(f"  The {goblin.name} hurls dark energy at you for {actual} damage.")


def print_state(player: Combatant, goblin: Combatant) -> None:
    g_hp = (
        f"{goblin.hp}/{goblin.max_hp} HP"
        if player.revealed
        else "??/?? HP"
    )
    print(
        f"\n  [You: {player.hp}/{player.max_hp} HP · {player.mana}/{player.max_mana} mana]"
        f"  [{goblin.name}: {g_hp}]"
    )


# ----------------------------------------------------------------------
# Loop
# ----------------------------------------------------------------------


def play(g: Grimoire, *, demo: bool, seed_rng: int = 42) -> None:
    rng = random.Random(seed_rng)
    player = Combatant("You", hp=50, max_hp=50, mana=100, max_mana=100)
    goblin = Combatant("Goblin Warlock", hp=60, max_hp=60)

    print(
        "\nA goblin warlock blocks the corridor. The grimoire hums under your hand.\n"
        "Speak incantations to cast — or `quit` to retreat.\n"
        f"(fizzle threshold: distance > {FIZZLE_THRESHOLD})"
    )
    print_state(player, goblin)

    demo_iter = iter(DEMO_INCANTATIONS) if demo else None

    while player.hp > 0 and goblin.hp > 0:
        if demo_iter is not None:
            try:
                line = next(demo_iter)
            except StopIteration:
                print("\n(end of scripted demo)")
                return
            print(f"\n> {line}")
        else:
            try:
                line = input("\n> ").strip()
            except EOFError:
                print()
                return
            if not line:
                continue
            if line.lower() in {"quit", "exit", ":q"}:
                print("You close the grimoire and back away.")
                return

        cast(g, line, player, goblin)
        if goblin.hp > 0:
            goblin_attack(player, goblin, rng)
        print_state(player, goblin)

    if player.hp <= 0:
        print("\nYou collapse. The goblin's laughter fades as the world dims.")
    elif goblin.hp <= 0:
        print("\nThe goblin warlock crumbles. The grimoire pulses with new heat.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Run a scripted demo scenario instead of reading stdin.",
    )
    args = parser.parse_args()

    create(MOUNT)
    should_seed = needs_seed(MOUNT.default_db)
    with Grimoire.open(
        MOUNT.default_db,
        embedder=FastembedEmbedder(cache_folder=MOUNT.models_dir),
    ) as g:
        if should_seed:
            print(f"Inscribing {len(SPELLS)} spells into {MOUNT.default_db}")
            seed(g)
        play(g, demo=args.demo)


if __name__ == "__main__":
    main()
