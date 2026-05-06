"""Informal perf pass over Grimoire's read surface.

Resets the db at `.grimoire/grimoire.db`, seeds a corpus via `add_many`,
then exercises get / list / vector_search / keyword_search at a few k and
limit values, with and without a `kind` filter.

Mode flags (mutex, default = clear db, seed fresh):
    --full-wipe   Wipe `.grimoire/` entirely — db and model cache. Forces
                  a re-download on the next run. Pytest's next run also
                  redownloads (same cache).
    --keep-data   Don't clear the db AND skip seeding. Read scenarios run
                  against whatever's in the db. Exits early if it's empty.

Requires the fastembed extra:
    uv sync --package grimoire --extra fastembed

Run from the repo root:
    uv run python scripts/perf/lib_reads.py [--corpus N] [--full-wipe | --keep-data]
"""

import argparse
import random
import time

from _common import (
    DB,
    MODE_DESCRIPTIONS,
    MODE_KEEP,
    Summary,
    add_wipe_flags,
    fmt,
    mode_from_args,
    prepare_grimoire,
    synth_records,
    time_each,
    time_n,
)
from grimoire import Grimoire

# Vector queries — natural-language phrasings whose embeddings should land
# near at least some seeded records.
VECTOR_QUERIES = (
    "a sleeping dragon under the mountain",
    "phoenix rising from ashes",
    "ancient guardian of the underworld",
    "creature that haunts ruins",
    "monster of the frostlands",
    "beast brooding in caves",
    "patrolling guardian beneath the city",
    "the kraken and the sunken city",
)

# Keyword queries — a mix of bare terms, booleans, and prefix matches that
# all parse as valid FTS5 syntax. Some return nothing; that still exercises
# the full search path.
KEYWORD_QUERIES = (
    "dragon",
    "underworld",
    "phoenix OR wraith",
    "legends AND century",
    "frost*",
    "guard*",
)


def run(corpus: int, mode: str) -> None:
    print(f"# library reads — corpus={corpus}, mode={mode}")
    print(f"# {MODE_DESCRIPTIONS[mode]}")

    summary = Summary("library reads")

    t0 = time.perf_counter()
    g = prepare_grimoire(mode)
    init_elapsed = time.perf_counter() - t0
    print(f"# init (mkdir + model load + warm probe): {fmt(init_elapsed).strip()}")

    seed_elapsed: float | None = None
    actual_corpus = 0
    try:
        if mode == MODE_KEEP:
            peek = Grimoire.peek(DB)
            actual_corpus = peek.entry_count if peek is not None else 0
            print(f"# keeping existing db: {actual_corpus} entries")
            if actual_corpus == 0:
                print("# nothing to read — skipping all scenarios")
        else:
            records = synth_records(corpus, seed=99)
            t0 = time.perf_counter()
            for i in range(0, corpus, 500):
                g.add_many(records[i : i + 500])
            seed_elapsed = time.perf_counter() - t0
            print(
                f"# seed ({corpus} records via add_many): {fmt(seed_elapsed).strip()}"
            )
            actual_corpus = corpus
        print()

        if actual_corpus > 0:
            # get — random-id lookups
            all_entries = g.list(limit=actual_corpus)
            rng = random.Random(7)
            sample_size = min(200, actual_corpus)
            sample_ids = [rng.choice(all_entries).id for _ in range(sample_size)]
            times = time_each(g.get, sample_ids)
            summary.each(f"get x {sample_size} (random ids)", times, group="get")

            # list — varying limits, no kind filter
            for limit in (10, 100, 1000):
                times = time_n(lambda lim=limit: g.list(limit=lim), 20)
                summary.each(f"list limit={limit} x 20", times, group="list")

            # list — with kind filter
            for limit in (10, 100):
                times = time_n(lambda lim=limit: g.list(kind="dragon", limit=lim), 20)
                summary.each(
                    f"list kind=dragon limit={limit} x 20",
                    times,
                    group="list (kind)",
                )

            # vector_search — varying k, no kind filter
            for k in (1, 10, 100):
                times = time_each(
                    lambda q, k=k: g.vector_search(q, k=k),
                    VECTOR_QUERIES,
                )
                summary.each(
                    f"vector_search k={k} x {len(VECTOR_QUERIES)}",
                    times,
                    group="vector_search",
                )

            # vector_search — with kind filter (pushed into vec partition key)
            for k in (1, 10, 100):
                times = time_each(
                    lambda q, k=k: g.vector_search(q, kind="dragon", k=k),
                    VECTOR_QUERIES,
                )
                summary.each(
                    f"vector_search kind=dragon k={k} x {len(VECTOR_QUERIES)}",
                    times,
                    group="vector_search (kind)",
                )

            # keyword_search — varying k, no kind filter
            for k in (1, 10, 100):
                times = time_each(
                    lambda q, k=k: g.keyword_search(q, k=k),
                    KEYWORD_QUERIES,
                )
                summary.each(
                    f"keyword_search k={k} x {len(KEYWORD_QUERIES)}",
                    times,
                    group="keyword_search",
                )

            # keyword_search — with kind filter (post-FTS join filter)
            for k in (1, 10, 100):
                times = time_each(
                    lambda q, k=k: g.keyword_search(q, kind="dragon", k=k),
                    KEYWORD_QUERIES,
                )
                summary.each(
                    f"keyword_search kind=dragon k={k} x {len(KEYWORD_QUERIES)}",
                    times,
                    group="keyword_search (kind)",
                )
    finally:
        g.close()

    extras = {"init": fmt(init_elapsed)}
    if seed_elapsed is not None:
        extras["seed"] = fmt(seed_elapsed)
    extras["corpus"] = f"{actual_corpus} entries"
    summary.render(extras=extras)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Informal perf pass over Grimoire's read surface."
    )
    parser.add_argument(
        "--corpus",
        type=int,
        default=2000,
        help="Number of seeded entries (default: 2000). Ignored under --keep-data.",
    )
    add_wipe_flags(parser)
    args = parser.parse_args()
    run(corpus=args.corpus, mode=mode_from_args(args))


if __name__ == "__main__":
    main()
