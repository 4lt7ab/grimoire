"""Snippet vault — FTS5 keyword search across code snippets.

Each entry stores {title, language, description, body} in `data` and
indexes title + description + body into entry_fts. Queries pass FTS5
syntax through: phrases, prefix (`*`), boolean (AND / OR / NOT).
No idx, no vec — keyword-only.
"""

from __future__ import annotations

from pathlib import Path

from grimoire.data.entry import Entry
from grimoire.embed import NoOpEmbedder
from grimoire.errors import GrimoireNotFound
from grimoire.grimoire import Grimoire
from grimoire.mount import Mount, create

MOUNT = Mount(path=Path(__file__).parent / ".grimoire")

SEEDS: list[dict] = [
    {
        "title": "Retry with exponential backoff",
        "language": "python",
        "description": (
            "Coroutine helper that retries an async call with exponential "
            "backoff and jitter."
        ),
        "body": (
            "async def retry(fn, *, attempts=5, base=0.1):\n"
            "    for i in range(attempts):\n"
            "        try: return await fn()\n"
            "        except Exception:\n"
            "            if i == attempts - 1: raise\n"
            "            await asyncio.sleep(base * (2 ** i) + random.random() * 0.1)\n"
        ),
    },
    {
        "title": "Postgres recursive CTE for parent traversal",
        "language": "sql",
        "description": (
            "Walk parent_id pointers up to the root of a tree using a "
            "recursive common table expression."
        ),
        "body": (
            "WITH RECURSIVE ancestors AS (\n"
            "    SELECT id, parent_id, name FROM nodes WHERE id = $1\n"
            "    UNION ALL\n"
            "    SELECT n.id, n.parent_id, n.name\n"
            "      FROM nodes n JOIN ancestors a ON n.id = a.parent_id\n"
            ")\n"
            "SELECT * FROM ancestors;\n"
        ),
    },
    {
        "title": "Rust async timeout wrapper",
        "language": "rust",
        "description": "Wrap a future in a timeout and map the elapsed error.",
        "body": (
            "pub async fn within<T>(d: Duration, fut: impl Future<Output = T>)\n"
            "    -> Result<T, Elapsed>\n"
            "{\n"
            "    tokio::time::timeout(d, fut).await\n"
            "}\n"
        ),
    },
    {
        "title": "Shell: kill a port hog",
        "language": "sh",
        "description": (
            "Find and kill the process listening on a TCP port — macOS and "
            "Linux compatible."
        ),
        "body": "kill -9 $(lsof -ti :$1)\n",
    },
    {
        "title": "Python generator: chunked",
        "language": "python",
        "description": (
            "Lazily yield fixed-size chunks from an iterable without "
            "materializing the whole thing."
        ),
        "body": (
            "def chunked(iterable, n):\n"
            "    buf = []\n"
            "    for item in iterable:\n"
            "        buf.append(item)\n"
            "        if len(buf) == n:\n"
            "            yield buf\n"
            "            buf = []\n"
            "    if buf:\n"
            "        yield buf\n"
        ),
    },
    {
        "title": "git: rebase onto upstream main",
        "language": "sh",
        "description": (
            "Fetch upstream and rebase the current branch onto its main, "
            "preserving merge commits."
        ),
        "body": "git fetch upstream && git rebase --rebase-merges upstream/main\n",
    },
    {
        "title": "Async semaphore-bounded gather",
        "language": "python",
        "description": (
            "Run coroutines concurrently with a max concurrency cap using "
            "asyncio.Semaphore."
        ),
        "body": (
            "async def gather_bounded(coros, *, limit=10):\n"
            "    sem = asyncio.Semaphore(limit)\n"
            "    async def runner(c):\n"
            "        async with sem: return await c\n"
            "    return await asyncio.gather(*(runner(c) for c in coros))\n"
        ),
    },
    {
        "title": "Postgres: upsert with ON CONFLICT",
        "language": "sql",
        "description": (
            "Insert or update a row keyed by a unique constraint, returning "
            "the affected row."
        ),
        "body": (
            "INSERT INTO sessions (user_id, last_seen) VALUES ($1, NOW())\n"
            "ON CONFLICT (user_id) DO UPDATE SET last_seen = EXCLUDED.last_seen\n"
            "RETURNING *;\n"
        ),
    },
    {
        "title": "Rust: collect Results into Result<Vec>",
        "language": "rust",
        "description": (
            "Short-circuit on the first Err while collecting an iterator "
            "of Results."
        ),
        "body": (
            "let parsed: Result<Vec<_>, _> = inputs.iter().map(parse).collect();\n"
        ),
    },
    {
        "title": "tar: extract a single file",
        "language": "sh",
        "description": "Pull one file out of a tarball without unpacking the rest.",
        "body": "tar -xzf archive.tar.gz path/to/file\n",
    },
]

QUERIES = [
    "python retry",
    "async",
    "postgres",
    '"on conflict"',
    "rebase*",
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
        g.index(
            created.uniq_id,
            match=f"{s['language']} {s['title']} {s['description']} {s['body']}",
        )


def demo(g: Grimoire) -> None:
    for q in QUERIES:
        print(f"\n? {q}")
        print("-" * (len(q) + 2))
        entries, hits = g.match(q, limit=3)
        if not entries:
            print("  (no hits)")
            continue
        for e, h in zip(entries, hits, strict=True):
            print(
                f"  score={h.score:6.2f}  "
                f"[{e.data['language']:6s}] {e.data['title']}"
            )


def main() -> None:
    create(MOUNT)
    should_seed = needs_seed(MOUNT.default_db)
    with Grimoire.open(MOUNT.default_db, embedder=NoOpEmbedder()) as g:
        if should_seed:
            print(f"Seeding {len(SEEDS)} snippets into {MOUNT.default_db}")
            seed(g)
        else:
            print(f"Using existing vault at {MOUNT.default_db}")
        demo(g)


if __name__ == "__main__":
    main()
