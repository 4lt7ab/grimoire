"""Bestiary — a creature catalog with a tiny CLI, built on grimoire.

Every command flows through the library's public API. The example fits in one
file because grimoire owns storage, embedding, and semantic search; all this
code does is shape arguments and print results.
"""

import argparse
from pathlib import Path

from grimoire import Grimoire
from grimoire.embedders import FastembedEmbedder

SCRIPT_DIR = Path(__file__).parent
LOCAL = SCRIPT_DIR / ".local"
DB = LOCAL / "data" / "bestiary.db"
MODELS = LOCAL / "models"


def cmd_add(g: Grimoire, args: argparse.Namespace) -> None:
    entry = g.add(kind=args.kind, content=args.description)
    print(f"Added {entry.id}  {entry.kind}: {entry.content}")


def cmd_find(g: Grimoire, args: argparse.Namespace) -> None:
    results = g.search(args.query, kind=args.kind, k=args.k)
    if not results:
        print("(no matching creatures)")
        return
    for r in results:
        print(f"{r.id}  {r.kind:>10}  d={r.distance:.3f}  {r.content}")


def cmd_list(g: Grimoire, args: argparse.Namespace) -> None:
    entries = g.list(kind=args.kind, limit=args.limit)
    if not entries:
        print("(empty bestiary)")
        return
    for e in entries:
        print(f"{e.id}  {e.kind:>10}  {e.content}")


def cmd_remove(g: Grimoire, args: argparse.Namespace) -> None:
    if g.delete(args.id):
        print(f"Removed {args.id}")
    else:
        print(f"No creature with id {args.id!r}")


def main() -> None:
    parser = argparse.ArgumentParser(description="A grimoire-backed bestiary.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    add = sub.add_parser("add", help="Catalog a new creature.")
    add.add_argument(
        "--kind", required=True, help="Creature class, e.g. dragon, phoenix."
    )
    add.add_argument("description", help="Free-form description.")
    add.set_defaults(func=cmd_add)

    find = sub.add_parser("find", help="Semantic search across the bestiary.")
    find.add_argument("query", help="Natural-language description.")
    find.add_argument("--kind", help="Restrict to one creature class.")
    find.add_argument("--k", type=int, default=5, help="Max results to return.")
    find.set_defaults(func=cmd_find)

    lst = sub.add_parser("list", help="Browse entries chronologically.")
    lst.add_argument("--kind", help="Restrict to one creature class.")
    lst.add_argument("--limit", type=int, default=20)
    lst.set_defaults(func=cmd_list)

    rm = sub.add_parser("remove", help="Delete a creature by id.")
    rm.add_argument("id")
    rm.set_defaults(func=cmd_remove)

    args = parser.parse_args()
    DB.parent.mkdir(parents=True, exist_ok=True)
    embedder = FastembedEmbedder(cache_folder=MODELS)
    with Grimoire.open(DB, embedder=embedder) as g:
        args.func(g, args)


if __name__ == "__main__":
    main()
