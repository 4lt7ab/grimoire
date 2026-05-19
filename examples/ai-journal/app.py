"""AI journal — semantic recall over personal log entries.

Each entry is a short narrative plus a tiny structured payload (date,
mood, text). The narrative is embedded into the vec0 sidecar; queries
are natural prose. No idx, no FTS — see the snippet-vault and
reading-list examples for those facets.
"""

from __future__ import annotations

from pathlib import Path

from grimoire.data.entry import Entry
from grimoire.embed import FastembedEmbedder
from grimoire.errors import GrimoireNotFound
from grimoire.grimoire import Grimoire
from grimoire.mount import Mount, create

MOUNT = Mount(path=Path(__file__).parent / ".grimoire")

SEEDS: list[dict] = [
    {
        "date": "2026-04-01",
        "mood": "drained",
        "text": (
            "Spent the afternoon chasing a Postgres replication lag that "
            "turned out to be a missing index on the audit table. Three hours."
        ),
    },
    {
        "date": "2026-04-03",
        "mood": "energized",
        "text": (
            "Pair-coded a refactor of the auth middleware with Priya. We "
            "extracted the token validator into its own module and the test "
            "suite shrank by two hundred lines."
        ),
    },
    {
        "date": "2026-04-05",
        "mood": "anxious",
        "text": (
            "Quarterly planning meeting ran long. Leadership wants to ship the "
            "billing migration by end of May; I don't think we have the runway."
        ),
    },
    {
        "date": "2026-04-07",
        "mood": "curious",
        "text": (
            "Read a paper on hierarchical navigable small worlds. The idea of "
            "stacking proximity graphs to make k-NN sub-logarithmic is gorgeous."
        ),
    },
    {
        "date": "2026-04-09",
        "mood": "satisfied",
        "text": (
            "Got the flaky CI job down from a 12% failure rate to under 1% by "
            "swapping the in-memory test cache for a real Redis container."
        ),
    },
    {
        "date": "2026-04-10",
        "mood": "frustrated",
        "text": (
            "The new feature flag system swallowed an exception silently and "
            "served stale config for six hours before anyone noticed."
        ),
    },
    {
        "date": "2026-04-12",
        "mood": "energized",
        "text": (
            "Day at the offsite mapping the team's on-call rotation to actual "
            "incident counts. We're paging the wrong person three nights a week."
        ),
    },
    {
        "date": "2026-04-15",
        "mood": "reflective",
        "text": (
            "Closed out the API redesign RFC. Two months of debate, three "
            "rewrites, and the final shape is basically what the staff engineer "
            "sketched on a whiteboard in week one."
        ),
    },
]

QUERIES = [
    "what was I working on related to databases?",
    "moments of collaboration with teammates",
    "incidents and on-call pain",
]


def needs_seed(db_path: Path) -> bool:
    if not db_path.exists() or db_path.stat().st_size == 0:
        return True
    try:
        peek = Grimoire.peek(db_path)
    except GrimoireNotFound:
        return True
    return peek.entry_count == 0


def seed(g: Grimoire) -> None:
    entries = [Entry(uniq_id=None, data=s) for s in SEEDS]
    inserted = g.add(entries)
    for created, s in zip(inserted, SEEDS, strict=True):
        g.index(created.uniq_id, search=s["text"])


def demo(g: Grimoire) -> None:
    for q in QUERIES:
        print(f"\n? {q}")
        print("-" * (len(q) + 2))
        entries, hits = g.search(q, limit=3)
        for e, h in zip(entries, hits, strict=True):
            snippet = e.data["text"][:78].rstrip()
            print(
                f"  d={h.distance:.3f}  "
                f"[{e.data['date']} · {e.data['mood']:11s}] {snippet}…"
            )


def main() -> None:
    create(MOUNT)
    should_seed = needs_seed(MOUNT.default_db)
    with Grimoire.open(
        MOUNT.default_db,
        embedder=FastembedEmbedder(cache_folder=MOUNT.models_dir),
    ) as g:
        if should_seed:
            print(f"Seeding {len(SEEDS)} journal entries into {MOUNT.default_db}")
            seed(g)
        else:
            print(f"Using existing journal at {MOUNT.default_db}")
        demo(g)


if __name__ == "__main__":
    main()
