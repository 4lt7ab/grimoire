"""Informal perf pass over the `grimoire` CLI's write surface.

Resets `.grimoire/`, runs `grimoire init` once, optionally pre-seeds via
`grimoire ingest`, then exercises add / ingest / update / update-many /
delete / delete-many. Each scenario step spawns a fresh subprocess, so
wall-clock timings are dominated by Python startup + embedder load on
single-call scenarios. The `ingest` / `update-many` / `delete-many` rows
amortize that startup across many records — the whole point of the
batch commands existing.

Mode flags (mutex, default = clear db only):
    --full-wipe   Wipe `.grimoire/` entirely — db, model cache, and the
                  scratch dir. Forces a re-download on the next
                  `grimoire init`. Pytest's next run also redownloads
                  (same cache).
    --keep-data   Don't clear the db. Run on top of existing state. Note:
                  update / delete scenarios mutate live entries.

--corpus N        Seed N records via `grimoire ingest` before scenarios
                  run. Default 0 = no seed. Useful for sizing tests; pair
                  with --keep-data on subsequent runs to skip re-seeding.

--scale defaults to 20 (not 200 like the library scripts) because each
single-call scenario is bounded by ~hundreds of ms per invocation.

Requires both packages installed with the fastembed extra:
    uv sync --package grimoire --extra fastembed
    uv sync --package grimoire-cli --extra fastembed

Run from the repo root:
    uv run python scripts/perf/cli_writes.py [--scale N] [--corpus N] \\
        [--full-wipe | --keep-data]
"""

import argparse

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
    write_ids_file,
    write_jsonl,
)
from grimoire import Grimoire


def _list_ids(limit: int) -> list[str]:
    """Pull the first `limit` entry ids from the db via `grimoire list`."""
    _, result = cli_run(["list", "--limit", str(limit)])
    return [r["id"] for r in parse_jsonl_output(result.stdout)]


def _list_entries(limit: int) -> list[dict]:
    """Pull the first `limit` entries (full records) for kind-flip updates."""
    _, result = cli_run(["list", "--limit", str(limit)])
    return parse_jsonl_output(result.stdout)


def run(scale: int, mode: str, corpus: int) -> None:
    print(f"# CLI writes — scale={scale}, corpus={corpus}, mode={mode}")
    print(f"# {MODE_DESCRIPTIONS[mode]}")

    summary = Summary("CLI writes")

    prepare_cli_mount(mode)

    # `grimoire init` — first measurement. In reset mode the model cache
    # survives, so this is fast; in full mode it re-downloads.
    init_elapsed, _ = cli_run(["init"])
    print(f"# init (grimoire init): {fmt(init_elapsed).strip()}")

    if mode == MODE_KEEP:
        peek = Grimoire.peek(DB)
        initial = peek.entry_count if peek is not None else 0
        print(f"# starting db state: {initial} entries")

    # Optional pre-seed via `grimoire ingest`. One subprocess regardless
    # of corpus size — amortizes the startup over a single ingest run.
    seed_elapsed: float | None = None
    if corpus > 0:
        records = synth_records(corpus, seed=99)
        seed_path = CLI_TMP_DIR / "seed.jsonl"
        write_jsonl(seed_path, records)
        seed_elapsed, _ = cli_run(["ingest", str(seed_path)])
        print(f"# seed ({corpus} records via ingest): {fmt(seed_elapsed).strip()}")
    print()

    # add — single CLI calls. Process startup × scale, the slow path.
    recs = synth_records(scale, seed=10)
    times = []
    for r in recs:
        t, _ = cli_run(["add", r["content"], "--kind", r["kind"]])
        times.append(t)
    summary.each(f"add x {scale}", times, group="add")

    # ingest — one subprocess per file size. Amortization story.
    for file_size in (10, 100, 1000):
        recs = synth_records(file_size, seed=20 + file_size)
        path = CLI_TMP_DIR / f"ingest_{file_size}.jsonl"
        write_jsonl(path, recs)
        t, _ = cli_run(["ingest", str(path)])
        summary.batch(f"ingest size={file_size}", t, 1, file_size, group="ingest")

    # update (content) — single CLI calls; re-embed path.
    ids = _list_ids(scale)
    times = []
    for i, eid in enumerate(ids):
        t, _ = cli_run(["update", eid, "--content", f"revised content #{i}"])
        times.append(t)
    summary.each(
        f"update x {len(ids)} (content → re-embed)",
        times,
        group="update (content)",
    )

    # update (kind only) — flip dragon ↔ wraith to guarantee a kind change
    # while keeping content untouched.
    entries = _list_entries(scale)
    times = []
    for e in entries:
        new_kind = "wraith" if e["kind"] == "dragon" else "dragon"
        t, _ = cli_run(["update", e["id"], "--kind", new_kind])
        times.append(t)
    summary.each(
        f"update x {len(entries)} (kind only → no re-embed)",
        times,
        group="update (kind-only)",
    )

    # update-many — bulk patch via JSONL file, one subprocess per size.
    for file_size in (10, 100):
        ids_chunk = _list_ids(file_size)
        if not ids_chunk:
            continue
        patches = [
            {"id": eid, "content": f"bulk revision v{file_size} #{i}"}
            for i, eid in enumerate(ids_chunk)
        ]
        path = CLI_TMP_DIR / f"update_many_{file_size}.jsonl"
        write_jsonl(path, patches)
        t, _ = cli_run(["update-many", str(path)])
        summary.batch(
            f"update-many size={len(ids_chunk)} (content)",
            t,
            1,
            len(ids_chunk),
            group="update-many",
        )

    # delete — single CLI calls.
    ids = _list_ids(scale)
    times = []
    for eid in ids:
        t, _ = cli_run(["delete", eid])
        times.append(t)
    summary.each(f"delete x {len(ids)}", times, group="delete")

    # delete-many — bulk delete via id-list file, one subprocess per size.
    for file_size in (10, 100):
        ids_chunk = _list_ids(file_size)
        if not ids_chunk:
            continue
        path = CLI_TMP_DIR / f"delete_ids_{file_size}.txt"
        write_ids_file(path, ids_chunk)
        t, _ = cli_run(["delete-many", str(path)])
        summary.batch(
            f"delete-many size={len(ids_chunk)}",
            t,
            1,
            len(ids_chunk),
            group="delete-many",
        )

    extras = {"init": fmt(init_elapsed)}
    if seed_elapsed is not None:
        extras["seed"] = fmt(seed_elapsed)
    peek = Grimoire.peek(DB)
    if peek is not None:
        size_mb = DB.stat().st_size / (1024 * 1024)
        extras["final state"] = f"{peek.entry_count} entries, {size_mb:.2f} MB"
    summary.render(extras=extras)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Informal perf pass over the grimoire CLI's write surface."
    )
    parser.add_argument(
        "--scale",
        type=int,
        default=20,
        help=(
            "Sample size for single-call scenarios (default: 20). Smaller "
            "than the library script default because each invocation pays "
            "process startup."
        ),
    )
    parser.add_argument(
        "--corpus",
        type=int,
        default=0,
        help=(
            "Seed N records via `grimoire ingest` before scenarios run "
            "(default: 0 = no seed). Pair with --keep-data on later runs."
        ),
    )
    add_wipe_flags(parser)
    args = parser.parse_args()
    run(scale=args.scale, mode=mode_from_args(args), corpus=args.corpus)


if __name__ == "__main__":
    main()
