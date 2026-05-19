"""Reading list — filterable catalog over articles to read.

Each entry stores {title, author, url} in `data`. The URL is also the
entry_idx uniq_ref (sparse-unique, so each URL maps to at most one
entry), and five ordinals carry structured metadata:

    ordinal_1 = status         ("unread" / "reading" / "done")
    ordinal_2 = priority       (1-5, int)
    ordinal_3 = added_date     ("YYYY-MM-DD", ISO sortable)
    ordinal_4 = est_word_count (int)
    ordinal_5 = source_kind    ("paper" / "blog" / "book" / "talk")

No FTS, no vec — structured filtering only.
"""

from __future__ import annotations

from pathlib import Path

from grimoire.data.entry import Entry, Filters
from grimoire.embed import NoOpEmbedder
from grimoire.errors import GrimoireNotFound
from grimoire.grimoire import Grimoire
from grimoire.mount import Mount, create

MOUNT = Mount(path=Path(__file__).parent / ".grimoire")

# (url, data, (status, priority, added_date, est_words, source_kind))
SEEDS: list[tuple[str, dict, tuple]] = [
    (
        "https://example.com/hnsw-paper",
        {
            "title": "Efficient and robust approximate nearest neighbor search using HNSW",
            "author": "Malkov & Yashunin",
            "url": "https://example.com/hnsw-paper",
        },
        ("done", 5, "2026-03-15", 8200, "paper"),
    ),
    (
        "https://example.com/jepsen-postgres",
        {
            "title": "Jepsen: PostgreSQL 12.3 analysis",
            "author": "Kingsbury",
            "url": "https://example.com/jepsen-postgres",
        },
        ("unread", 5, "2026-04-02", 12500, "blog"),
    ),
    (
        "https://example.com/raft-extended",
        {
            "title": "In Search of an Understandable Consensus Algorithm (extended)",
            "author": "Ongaro & Ousterhout",
            "url": "https://example.com/raft-extended",
        },
        ("reading", 4, "2026-04-08", 17500, "paper"),
    ),
    (
        "https://example.com/designing-data-intensive",
        {
            "title": "Designing Data-Intensive Applications",
            "author": "Kleppmann",
            "url": "https://example.com/designing-data-intensive",
        },
        ("reading", 5, "2026-02-20", 180000, "book"),
    ),
    (
        "https://example.com/llm-eval-blog",
        {
            "title": "How to evaluate LLM applications in production",
            "author": "Anonymous",
            "url": "https://example.com/llm-eval-blog",
        },
        ("unread", 3, "2026-04-12", 2400, "blog"),
    ),
    (
        "https://example.com/fastembed-tutorial",
        {
            "title": "FastEmbed: local ONNX embeddings in three lines",
            "author": "Qdrant blog",
            "url": "https://example.com/fastembed-tutorial",
        },
        ("done", 2, "2026-03-28", 1100, "blog"),
    ),
    (
        "https://example.com/sqlite-vec-intro",
        {
            "title": "sqlite-vec: vector search in a single file",
            "author": "Asg017",
            "url": "https://example.com/sqlite-vec-intro",
        },
        ("done", 4, "2026-03-30", 1800, "blog"),
    ),
    (
        "https://example.com/lsm-tree-survey",
        {
            "title": "LSM-based storage techniques: a survey",
            "author": "Luo & Carey",
            "url": "https://example.com/lsm-tree-survey",
        },
        ("unread", 4, "2026-04-14", 22000, "paper"),
    ),
    (
        "https://example.com/wal-talk",
        {
            "title": "Write-ahead logs at scale (StrangeLoop)",
            "author": "Various",
            "url": "https://example.com/wal-talk",
        },
        ("unread", 2, "2026-04-11", 0, "talk"),
    ),
    (
        "https://example.com/site-reliability-workbook",
        {
            "title": "Site Reliability Workbook — Postmortem chapter",
            "author": "Beyer et al.",
            "url": "https://example.com/site-reliability-workbook",
        },
        ("reading", 3, "2026-03-05", 9000, "book"),
    ),
    (
        "https://example.com/btreemap-blog",
        {
            "title": "Why BTreeMap is so much faster than HashMap (sometimes)",
            "author": "Rust internals",
            "url": "https://example.com/btreemap-blog",
        },
        ("unread", 3, "2026-04-15", 2800, "blog"),
    ),
    (
        "https://example.com/spanner-paper",
        {
            "title": "Spanner: Google's globally-distributed database",
            "author": "Corbett et al.",
            "url": "https://example.com/spanner-paper",
        },
        ("done", 5, "2026-01-22", 14000, "paper"),
    ),
]


QUERIES: list[tuple[str, Filters]] = [
    (
        "Unread items, priority >= 4 (read these next)",
        Filters(equals={"ordinal_1": ["unread"]}, gte={"ordinal_2": 4}),
    ),
    (
        "Quick reads (under 3000 estimated words)",
        Filters(lte={"ordinal_4": 3000}),
    ),
    (
        "Added since April 1",
        Filters(gte={"ordinal_3": "2026-04-01"}),
    ),
    (
        "Currently reading",
        Filters(equals={"ordinal_1": ["reading"]}),
    ),
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
    entries = [Entry(uniq_id=None, data=data) for _, data, _ in SEEDS]
    inserted = g.add(entries)
    for created, (url, _, ords) in zip(inserted, SEEDS, strict=True):
        g.index(created.uniq_id, ref=url, ord=ords)


def _row(e: Entry, i) -> str:
    return (
        f"  [{i.ordinal_1:7s} p{i.ordinal_2}  "
        f"{i.ordinal_3}  {i.ordinal_4:>6}w  {i.ordinal_5:5s}] "
        f"{e.data['title'][:62]}"
    )


def demo(g: Grimoire) -> None:
    for label, filters in QUERIES:
        print(f"\n? {label}")
        print("-" * (len(label) + 2))
        entries, idxs = g.query(filters, limit=10)
        if not entries:
            print("  (no hits)")
            continue
        for e, i in zip(entries, idxs, strict=True):
            print(_row(e, i))

    print("\n? fetch by URL (uniq_ref lookup)")
    print("---------------------------------")
    url = "https://example.com/hnsw-paper"
    entries, idxs = g.fetch([url])
    for e, i in zip(entries, idxs, strict=True):
        print(f"  {e.data['title']}")
        print(f"    by {e.data['author']}  ·  {i.ordinal_1} · priority {i.ordinal_2}")


def main() -> None:
    create(MOUNT)
    should_seed = needs_seed(MOUNT.default_db)
    with Grimoire.open(MOUNT.default_db, embedder=NoOpEmbedder()) as g:
        if should_seed:
            print(f"Seeding {len(SEEDS)} reading-list items into {MOUNT.default_db}")
            seed(g)
            g.analyze()
        else:
            print(f"Using existing reading list at {MOUNT.default_db}")
        demo(g)


if __name__ == "__main__":
    main()
