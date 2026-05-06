"""Informal perf pass over the `grimoire` CLI's read surface.

Resets `.grimoire/`, runs `grimoire init`, optionally seeds via
`grimoire ingest`, then exercises info / list / get / vector-search /
keyword-search at a few k and limit values, with and without a `kind`
filter. Each scenario step spawns a fresh subprocess.

`info` is the cheapest call — it uses `Grimoire.peek` and never loads
the embedder, so its row shows pure CLI startup cost (Python + imports +
sqlite peek). Subtract it from the embedder-loading rows to see the
fastembed warm-load tax.

Mode flags (mutex, default = clear db, seed fresh):
    --full-wipe   Wipe `.grimoire/` entirely — db, model cache, and the
                  scratch dir. Forces a re-download on the next
                  `grimoire init`. Pytest's next run also redownloads
                  (same cache).
    --keep-data   Don't clear the db AND skip seeding. Read scenarios
                  run against whatever's in the db. Exits early if it's
                  empty.

--corpus N        Seed N records via `grimoire ingest` before scenarios
                  run. Default 2000. Ignored under --keep-data.

--scale defaults to 20 because each invocation pays ~hundreds of ms of
process startup; running every scenario 200× would burn an unnecessary
amount of wall time.

Requires both packages installed with the fastembed extra:
    uv sync --package grimoire --extra fastembed
    uv sync --package grimoire-cli --extra fastembed

Run from the repo root:
    uv run python scripts/perf/cli_reads.py [--scale N] [--corpus N] \\
        [--full-wipe | --keep-data]
"""

import argparse
import random
from collections.abc import Callable

from _common import (
    CLI_TMP_DIR,
    DB,
    MODE_DESCRIPTIONS,
    MODE_KEEP,
    Summary,
    add_wipe_flags,
    cli_run,
    fmt,
    mode_from_args,
    parse_jsonl_output,
    prepare_cli_mount,
    synth_records,
    write_jsonl,
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

# Keyword queries — bare words, booleans, and prefix matches that all
# parse as valid FTS5. Some return nothing; that still exercises the path.
KEYWORD_QUERIES = (
    "dragon",
    "underworld",
    "phoenix OR wraith",
    "legends AND century",
    "frost*",
    "guard*",
)


def _times_repeated(args: list[str], reps: int) -> list[float]:
    """Run `grimoire <args>` `reps` times, return per-call elapsed seconds."""
    times: list[float] = []
    for _ in range(reps):
        t, _ = cli_run(args)
        times.append(t)
    return times


def _times_per_query(
    args_for: Callable[[str], list[str]], queries: tuple[str, ...]
) -> list[float]:
    """For each query, build args via `args_for(query)`, run once, time it."""
    times: list[float] = []
    for q in queries:
        t, _ = cli_run(args_for(q))
        times.append(t)
    return times


def run(scale: int, mode: str, corpus: int) -> None:
    print(f"# CLI reads — scale={scale}, corpus={corpus}, mode={mode}")
    print(f"# {MODE_DESCRIPTIONS[mode]}")

    summary = Summary("CLI reads")

    prepare_cli_mount(mode)

    # `grimoire init` — first measurement. Reset mode keeps the model cache
    # so this is fast; full mode redownloads.
    init_elapsed, _ = cli_run(["init"])
    print(f"# init (grimoire init): {fmt(init_elapsed).strip()}")

    seed_elapsed: float | None = None
    actual_corpus = 0
    if mode == MODE_KEEP:
        peek = Grimoire.peek(DB)
        actual_corpus = peek.entry_count if peek is not None else 0
        print(f"# keeping existing db: {actual_corpus} entries")
        if actual_corpus == 0:
            print("# nothing to read — skipping all scenarios")
    else:
        records = synth_records(corpus, seed=99)
        seed_path = CLI_TMP_DIR / "seed.jsonl"
        write_jsonl(seed_path, records)
        seed_elapsed, _ = cli_run(["ingest", str(seed_path)])
        print(f"# seed ({corpus} records via ingest): {fmt(seed_elapsed).strip()}")
        actual_corpus = corpus
    print()

    if actual_corpus > 0:
        # info — baseline. No embedder load; pure CLI startup.
        times = _times_repeated(["info"], scale)
        summary.each(f"info x {scale}", times, group="info")

        # list — varying limits, no kind filter
        for limit in (10, 100, 1000):
            times = _times_repeated(["list", "--limit", str(limit)], scale)
            summary.each(f"list limit={limit} x {scale}", times, group="list")

        # list — with kind filter
        for limit in (10, 100):
            times = _times_repeated(
                ["list", "--limit", str(limit), "--kind", "dragon"], scale
            )
            summary.each(
                f"list kind=dragon limit={limit} x {scale}",
                times,
                group="list (kind)",
            )

        # get — random-id lookups
        _, listing = cli_run(["list", "--limit", str(actual_corpus)])
        all_ids = [r["id"] for r in parse_jsonl_output(listing.stdout)]
        rng = random.Random(7)
        sample_size = min(scale, len(all_ids))
        sample_ids = [rng.choice(all_ids) for _ in range(sample_size)]
        times: list[float] = []
        for eid in sample_ids:
            t, _ = cli_run(["get", eid])
            times.append(t)
        summary.each(f"get x {sample_size} (random ids)", times, group="get")

        # vector-search — varying k, no kind filter
        for k in (1, 10, 100):
            times = _times_per_query(
                lambda q, k=k: ["vector-search", q, "--k", str(k)],
                VECTOR_QUERIES,
            )
            summary.each(
                f"vector-search k={k} x {len(VECTOR_QUERIES)}",
                times,
                group="vector-search",
            )

        # vector-search — with kind filter (pushed into vec partition key)
        for k in (1, 10, 100):
            times = _times_per_query(
                lambda q, k=k: [
                    "vector-search",
                    q,
                    "--k",
                    str(k),
                    "--kind",
                    "dragon",
                ],
                VECTOR_QUERIES,
            )
            summary.each(
                f"vector-search kind=dragon k={k} x {len(VECTOR_QUERIES)}",
                times,
                group="vector-search (kind)",
            )

        # keyword-search — varying k, no kind filter
        for k in (1, 10, 100):
            times = _times_per_query(
                lambda q, k=k: ["keyword-search", q, "--k", str(k)],
                KEYWORD_QUERIES,
            )
            summary.each(
                f"keyword-search k={k} x {len(KEYWORD_QUERIES)}",
                times,
                group="keyword-search",
            )

        # keyword-search — with kind filter (post-FTS join filter)
        for k in (1, 10, 100):
            times = _times_per_query(
                lambda q, k=k: [
                    "keyword-search",
                    q,
                    "--k",
                    str(k),
                    "--kind",
                    "dragon",
                ],
                KEYWORD_QUERIES,
            )
            summary.each(
                f"keyword-search kind=dragon k={k} x {len(KEYWORD_QUERIES)}",
                times,
                group="keyword-search (kind)",
            )

    extras = {"init": fmt(init_elapsed)}
    if seed_elapsed is not None:
        extras["seed"] = fmt(seed_elapsed)
    extras["corpus"] = f"{actual_corpus} entries"
    summary.render(extras=extras)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Informal perf pass over the grimoire CLI's read surface."
    )
    parser.add_argument(
        "--scale",
        type=int,
        default=20,
        help=(
            "Repetitions for info / list / get scenarios (default: 20). "
            "vector-search and keyword-search iterate over their fixed "
            "query lists, so this only affects the per-call repetitions."
        ),
    )
    parser.add_argument(
        "--corpus",
        type=int,
        default=2000,
        help=(
            "Number of records to seed via `grimoire ingest` "
            "(default: 2000). Ignored under --keep-data."
        ),
    )
    add_wipe_flags(parser)
    args = parser.parse_args()
    run(scale=args.scale, mode=mode_from_args(args), corpus=args.corpus)


if __name__ == "__main__":
    main()
