"""Wizard's Errand — a small text adventure powered by grimoire.

A best-practice walkthrough of the library API in one runnable file:

* Mount-aware lifecycle: `Grimoire.mount` → `mount.has(name)` → `Grimoire.create`
  on first run, then `Grimoire.open` for every subsequent invocation.
* Bulk import on a fresh database via `add_many`, gated by a cheap presence
  check so re-running the example never re-imports.
* Reads use the right tool for each job: `list(group_key=...)` for partitioned
  browsing, `get_by_group_ref` for stable named lookups (places, the goal),
  and `vector_search` for the player's free-form action against their kit.
* No private imports, no manual cache plumbing — `Grimoire.open` auto-loads
  fastembed from the file's lock row using the shared `<mount>/models/` cache.

The game: a wizard sets out to retrieve the elder wand from the drowned
cathedral. Pick a random kit (three spells, three items) from the bestiary;
at each place a beast appears; describe what you want to do, the grimoire
finds the best fit in your kit. If nothing crosses the action threshold,
the encounter is lost.

Run it:

    python examples/errand.py
    python examples/errand.py --reset  # wipe and rebuild the database

The example owns its own mount at `examples/.local/`, so it never touches
your `~/.grimoire`.
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

from grimoire import Entry, Grimoire
from grimoire.embedders import FastembedEmbedder

SCRIPT_DIR = Path(__file__).parent
LOCAL = SCRIPT_DIR / ".local"  # this example's mount
NAME = "errand"  # the named database within the mount
FIXTURE = SCRIPT_DIR / "sample_grimoire.jsonl"

# Vector-distance below which a kit-member counts as a fit for the player's
# typed action. Tuned empirically against `sample_grimoire.jsonl` for the
# bundled fastembed default (BAAI/bge-small-en-v1.5, L2 distance): clear-
# intent phrasings tend to land in the 0.75-0.85 range, unrelated drift sits
# at 0.9+. Lower for stricter, higher for more forgiving.
ACTION_THRESHOLD = 0.9

# Three named places from the fixture, traversed in order. Each is fetched
# by `group_ref` — a stable consumer-set identifier — rather than by
# semantic search, because the route is fixed game data.
ROUTE = ("whispering-glade", "starfall-observatory", "drowned-cathedral")
GOAL_GROUP = "item"
GOAL_REF = "elder-wand"


# ---------- grimoire setup -------------------------------------------------


def load_fixture_if_empty(g: Grimoire) -> int:
    """Bulk-import `sample_grimoire.jsonl` into a fresh database.

    Idempotency is the cheap kind: peek for any entry; if one exists, the
    database is already populated and this is a no-op. The fixture is
    additive otherwise, but for example runs we want exactly one import.
    """
    if g.list(limit=1):
        return 0
    records: list[dict] = []
    with FIXTURE.open(encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if line:
                records.append(json.loads(line))
    g.add_many(records)
    return len(records)


# ---------- gameplay -------------------------------------------------------


def roll_kit(g: Grimoire, *, n_spells: int = 3, n_items: int = 3) -> list[Entry]:
    """Pick a random starting kit: a few spells and a few items.

    `list(group_key=...)` keeps each query partitioned at the vector index
    level — no need to fetch everything and filter in Python.
    """
    spells = g.list(group_key="spell", limit=100)
    items = g.list(group_key="item", limit=200)
    return random.sample(spells, n_spells) + random.sample(items, n_items)


def show_kit(kit: list[Entry]) -> None:
    print("\n  Your kit:")
    for entry in kit:
        kind = (entry.group_key or "?").ljust(5)
        print(f"    {kind}  {entry.group_ref}: {_first_clause(entry.content)}")


def _first_clause(s: str, limit: int = 80) -> str:
    """Trim a description to its first clause for compact display."""
    head = s.split(";", 1)[0]
    return head if len(head) <= limit else head[: limit - 1] + "…"


def random_beast(g: Grimoire) -> Entry:
    """Pick a random beast from the bestiary."""
    return random.choice(g.list(group_key="beast", limit=100))


def encounter(g: Grimoire, place_ref: str, kit_ids: set[str]) -> bool:
    """Run one encounter at a place. Returns True if the player advances.

    The action-resolution mechanic is a vector search: the player types
    free-form intent, the grimoire ranks every entry by similarity, we
    keep the kit-only matches, and the closest one decides the outcome
    (provided it clears `ACTION_THRESHOLD`).
    """
    place = g.get_by_group_ref(group_key="place", group_ref=place_ref)
    if place is None:
        # The fixture must have changed — fail loudly rather than silently
        # advancing past a missing place.
        raise RuntimeError(f"Place {place_ref!r} not found in the grimoire")

    beast = random_beast(g)
    print(f"\n--- {place.group_ref} ---")
    print(f"  {place.content}")
    print(f"\n  A {beast.group_ref} stands in your path.")
    print(f"  {beast.content}")

    for _ in range(3):
        try:
            action = input("\n  > ").strip()
        except EOFError:
            return False
        if not action:
            continue

        # Vector search across the entire grimoire, then filter to the
        # player's kit. Pulling k=30 gives the kit-overlap a reasonable
        # window even when the action's true neighbors are mostly outside
        # the kit. The list comes back sorted by ascending distance, so
        # `in_kit[0]` is automatically the best kit-match.
        candidates = g.vector_search(action, k=30)
        in_kit = [c for c in candidates if c.id in kit_ids]
        if not in_kit:
            print("  (nothing in your kit fits that approach)")
            continue

        best = in_kit[0]
        if best.distance is None or best.distance > ACTION_THRESHOLD:
            print(f"  You consider {best.group_ref}, but it doesn't quite fit.")
            continue

        print(f"  You wield {best.group_ref}: {_first_clause(best.content)}")
        print(f"  The {beast.group_ref} falls back. You press on.")
        return True

    print(f"\n  The {beast.group_ref} closes in. The errand ends here.")
    return False


def victory(g: Grimoire) -> None:
    """Final step: fetch the goal item by its stable group_ref and reveal it."""
    goal = g.get_by_group_ref(group_key=GOAL_GROUP, group_ref=GOAL_REF)
    if goal is not None:
        print(f"\n  You take it from the cathedral altar: {goal.content}")
    print("\nThe errand is complete.")


def play(g: Grimoire) -> None:
    print("\nA wizard sets out on an errand: retrieve the elder wand")
    print("from the drowned cathedral.")
    kit = roll_kit(g)
    kit_ids = {entry.id for entry in kit}
    show_kit(kit)
    for place_ref in ROUTE:
        if not encounter(g, place_ref, kit_ids):
            return
    victory(g)


# ---------- entry point ----------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="A small grimoire-backed adventure.")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Wipe and rebuild the example's database from the fixture.",
    )
    args = parser.parse_args()

    # Resolve and prepare the mount. This creates `LOCAL/` and `LOCAL/models/`
    # if they don't exist; it does NOT create any database. Manifest writes
    # are lazy — they happen on the first named-DB create.
    mount = Grimoire.mount(LOCAL)

    if args.reset:
        # Idempotent: missing files and manifest entries are tolerated.
        Grimoire.destroy(NAME, mount=LOCAL)

    try:
        if not mount.has(NAME):
            # Only the create path needs to instantiate fastembed; subsequent
            # runs auto-load it inside `Grimoire.open` from the lock row.
            embedder = FastembedEmbedder(cache_folder=LOCAL / "models")
            Grimoire.create(NAME, embedder=embedder, mount=LOCAL).close()
        with Grimoire.open(NAME, mount=LOCAL) as g:
            imported = load_fixture_if_empty(g)
            if imported:
                print(f"Imported {imported} records from {FIXTURE.name}.")
            try:
                play(g)
            except KeyboardInterrupt:
                print("\nThe wizard slips away.")
    except ImportError as exc:
        # The fastembed extra is optional; if it's missing, both `create`
        # and `open` raise ImportError with a helpful install hint. Wrap
        # here so a missing extra prints cleanly instead of as a traceback.
        raise SystemExit(f"Error: {exc}") from exc


if __name__ == "__main__":
    main()
