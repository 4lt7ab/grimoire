"""Riddle Tower — a semantic-search game built on grimoire.

Each floor's accepted answer phrasings live in the grimoire with their own
`threshold`. Type a guess; if any phrasing matches under its threshold, the
door opens. Progress is persisted; quit and resume any time.

Thresholds below are tuned for the default embedder (BAAI/bge-small-en-v1.5
via fastembed, L2 distance). Lower threshold = stricter match.
"""

import json
from pathlib import Path

from grimoire import Grimoire
from grimoire.embedders import FastembedEmbedder

SCRIPT_DIR = Path(__file__).parent
LOCAL = SCRIPT_DIR / ".local"
DB = LOCAL / "data" / "tower.db"
MODELS = LOCAL / "models"

RIDDLES = [
    {
        "floor": 1,
        "question": "I get wetter the more I dry. What am I?",
        "answers": ["a towel", "towel", "fluffy cloth used after a bath"],
        "threshold": 0.7,
    },
    {
        "floor": 2,
        "question": (
            "I have keys but no locks, space but no rooms. "
            "You can enter but not go inside. What am I?"
        ),
        "answers": ["a keyboard", "keyboard", "a computer keyboard"],
        "threshold": 0.55,
    },
    {
        "floor": 3,
        "question": "The more you take, the more you leave behind. What are they?",
        "answers": ["footsteps", "footprints", "the steps you have taken"],
        "threshold": 0.6,
    },
    {
        "floor": 4,
        "question": (
            "I speak without a mouth and hear without ears. "
            "I have no body, but I come alive with the wind."
        ),
        "answers": ["an echo", "echo", "a returning sound"],
        "threshold": 0.5,
    },
    {
        "floor": 5,
        "question": (
            "I am taken from a mine, shut up in a wooden case, "
            "and never released, yet used by almost everyone."
        ),
        "answers": ["graphite", "pencil lead", "graphite in a pencil"],
        "threshold": 0.45,
    },
]


def seed_if_empty(g: Grimoire) -> None:
    if g.list(kind="floor_1"):
        return
    for riddle in RIDDLES:
        for phrase in riddle["answers"]:
            g.add(
                kind=f"floor_{riddle['floor']}",
                content=phrase,
                threshold=riddle["threshold"],
            )


def current_floor(g: Grimoire) -> int:
    progress = g.list(kind="progress", limit=1000)
    if not progress:
        return 1
    return max(json.loads(e.payload)["floor"] for e in progress if e.payload)


def advance(g: Grimoire, floor: int) -> None:
    g.add(
        kind="progress",
        content=f"Ascended to floor {floor}",
        payload={"floor": floor},
    )


def play(g: Grimoire) -> None:
    while True:
        floor = current_floor(g)
        if floor > len(RIDDLES):
            print("\n✨ The tower is conquered. Nothing remains but legend.")
            return

        riddle = RIDDLES[floor - 1]
        print(f"\n— Floor {floor} —")
        print(riddle["question"])

        while True:
            try:
                guess = input("\n> ").strip()
            except EOFError:
                print()
                return
            if not guess:
                print("(give an answer, or type 'quit')")
                continue
            if guess.lower() in {"quit", "exit", "leave"}:
                print("You retreat down the stairs. The tower waits.")
                return

            matches = g.search(
                guess, kind=f"floor_{floor}", k=3, dynamic_threshold=True
            )
            if matches:
                advance(g, floor + 1)
                print("✓ The door creaks open.")
                break
            print("✗ The door does not move. Try again.")


def main() -> None:
    DB.parent.mkdir(parents=True, exist_ok=True)
    embedder = FastembedEmbedder(cache_folder=MODELS)
    with Grimoire.open(DB, embedder=embedder) as g:
        seed_if_empty(g)
        try:
            play(g)
        except KeyboardInterrupt:
            print("\nYou flee into the night.")


if __name__ == "__main__":
    main()
