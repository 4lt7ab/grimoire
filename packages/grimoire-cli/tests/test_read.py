import json
from pathlib import Path

from grimoire_cli.main import app
from typer.testing import CliRunner


def _invoke(runner: CliRunner, mount: Path, *args: str):
    return runner.invoke(app, ["--mount", str(mount), *args])


def _mounted(runner: CliRunner, mount: Path):
    assert _invoke(runner, mount, "mount").exit_code == 0


def _seed(runner: CliRunner, mount: Path, n: int, *, group: str = "spell") -> list[str]:
    ids: list[str] = []
    for i in range(n):
        result = _invoke(
            runner,
            mount,
            "entry",
            "add",
            "--group-key",
            group,
            "--payload",
            json.dumps({"i": i}),
        )
        ids.append(json.loads(result.stdout)["id"])
    return ids


def test_query_default_orders_chronologically(runner: CliRunner, mount: Path):
    _mounted(runner, mount)
    ids = _seed(runner, mount, 3)

    result = _invoke(runner, mount, "query")
    assert result.exit_code == 0, result.stderr
    rows = [json.loads(line) for line in result.stdout.strip().splitlines()]
    assert [r["id"] for r in rows] == sorted(ids)


def test_query_filters_by_group_key(runner: CliRunner, mount: Path):
    _mounted(runner, mount)
    spells = _seed(runner, mount, 2, group="spell")
    _seed(runner, mount, 3, group="item")

    result = _invoke(runner, mount, "query", "--group-key", "spell")
    assert result.exit_code == 0, result.stderr
    rows = [json.loads(line) for line in result.stdout.strip().splitlines()]
    assert {r["id"] for r in rows} == set(spells)


def test_query_cursor_pagination(runner: CliRunner, mount: Path):
    _mounted(runner, mount)
    ids = sorted(_seed(runner, mount, 5))

    first = _invoke(runner, mount, "query", "--limit", "2")
    page1 = [json.loads(line) for line in first.stdout.strip().splitlines()]
    assert [r["id"] for r in page1] == ids[:2]

    second = _invoke(runner, mount, "query", "--limit", "2", "--cursor", ids[1])
    page2 = [json.loads(line) for line in second.stdout.strip().splitlines()]
    assert [r["id"] for r in page2] == ids[2:4]


def test_search_keyword_returns_score(runner: CliRunner, mount: Path):
    _mounted(runner, mount)
    runner.invoke(
        app,
        ["--mount", str(mount), "create", "kw", "--embedder", "noop"],
    )
    # Insert directly via the library so we can exercise keyword_text.
    from grimoire.data.entry import Entry
    from grimoire.embed import NoOpEmbedder
    from grimoire.grimoire import open as open_grimoire

    g = open_grimoire(mount / "kw" / "grimoire.db", embedder=NoOpEmbedder())
    g.add(
        [
            Entry(None, None, None, None, keyword_text="phoenix down"),
            Entry(None, None, None, None, keyword_text="elder wand"),
        ]
    )
    g._conn.commit()
    g._conn.close()

    result = _invoke(
        runner, mount, "--db", "kw", "search", "phoenix", "--mode", "keyword"
    )
    assert result.exit_code == 0, result.stderr
    rows = [json.loads(line) for line in result.stdout.strip().splitlines()]
    assert len(rows) == 1
    assert "score" in rows[0]


def test_search_vector_noop_returns_partition(runner: CliRunner, mount: Path):
    _mounted(runner, mount)
    _seed(runner, mount, 2, group="spell")

    result = _invoke(
        runner, mount, "search", "anything", "--group-key", "spell", "-k", "5"
    )
    # NoOp embedder returns zero vectors; results are partition-scoped.
    # With no semantic_text set, entries aren't in the vector index at all.
    assert result.exit_code == 0, result.stderr
    # No entries had semantic_text set, so vec index is empty.
    assert result.stdout.strip() == ""
