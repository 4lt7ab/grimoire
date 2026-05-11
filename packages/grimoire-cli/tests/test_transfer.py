import json
from pathlib import Path

from grimoire_cli.main import app
from typer.testing import CliRunner


def _invoke(runner: CliRunner, mount: Path, *args: str):
    return runner.invoke(app, ["--mount", str(mount), *args])


def _mounted(runner: CliRunner, mount: Path):
    assert _invoke(runner, mount, "mount").exit_code == 0


def _seed(runner: CliRunner, mount: Path):
    _invoke(
        runner,
        mount,
        "entry",
        "add",
        "--group-key",
        "spell",
        "--group-ref",
        "fireball",
        "--payload",
        json.dumps({"power": 5}),
        "--keyword-text",
        "fireball hot",
        "--context",
        "core kit",
    )
    _invoke(
        runner,
        mount,
        "entry",
        "add",
        "--group-key",
        "item",
        "--group-ref",
        "phoenix-down",
        "--payload",
        json.dumps({"revives": True}),
    )


def test_export_stdout_emits_jsonl(runner: CliRunner, mount: Path, tmp_path: Path):
    _mounted(runner, mount)
    _seed(runner, mount)

    result = _invoke(runner, mount, "export")
    assert result.exit_code == 0, result.stderr
    rows = [json.loads(line) for line in result.stdout.strip().splitlines()]
    assert len(rows) == 2
    by_ref = {r["group_ref"]: r for r in rows}
    assert by_ref["fireball"]["payload"] == {"power": 5}
    assert by_ref["fireball"]["keyword_text"] == "fireball hot"
    assert by_ref["fireball"]["context"] == "core kit"
    # `id` is included for inspection; `semantic_text` was never set, so absent.
    assert "id" in by_ref["fireball"]
    assert "semantic_text" not in by_ref["fireball"]


def test_export_to_file(runner: CliRunner, mount: Path, tmp_path: Path):
    _mounted(runner, mount)
    _seed(runner, mount)
    target = tmp_path / "out.jsonl"

    result = _invoke(runner, mount, "export", "-o", str(target))
    assert result.exit_code == 0, result.stderr
    lines = [
        json.loads(line) for line in target.read_text().splitlines() if line.strip()
    ]
    assert len(lines) == 2


def test_export_refuses_overwrite_without_force(runner, mount, tmp_path):
    _mounted(runner, mount)
    _seed(runner, mount)
    target = tmp_path / "out.jsonl"
    target.write_text("preexisting\n")

    refuse = _invoke(runner, mount, "export", "-o", str(target))
    assert refuse.exit_code == 1
    assert "--force" in refuse.stderr

    forced = _invoke(runner, mount, "export", "-o", str(target), "--force")
    assert forced.exit_code == 0


def test_import_round_trips(runner: CliRunner, mount: Path, tmp_path: Path):
    _mounted(runner, mount)
    _seed(runner, mount)
    target = tmp_path / "out.jsonl"
    _invoke(runner, mount, "export", "-o", str(target))

    fresh = tmp_path / "fresh-mount"
    _invoke(runner, fresh, "mount")

    imp = _invoke(runner, fresh, "import", str(target))
    assert imp.exit_code == 0, imp.stderr

    listed = _invoke(runner, fresh, "query")
    rows = [json.loads(line) for line in listed.stdout.strip().splitlines()]
    assert len(rows) == 2
    refs = {r["group_ref"] for r in rows}
    assert refs == {"fireball", "phoenix-down"}


def test_import_rejects_duplicate_pairs_within_file(
    runner: CliRunner, mount: Path, tmp_path: Path
):
    _mounted(runner, mount)
    target = tmp_path / "dup.jsonl"
    target.write_text(
        "\n".join(
            [
                json.dumps({"group_key": "spell", "group_ref": "fireball"}),
                json.dumps({"group_key": "spell", "group_ref": "fireball"}),
            ]
        )
    )

    result = _invoke(runner, mount, "import", str(target))
    assert result.exit_code == 1
    assert "duplicate" in result.stderr.lower()


def test_import_rejects_existing_pairs(runner: CliRunner, mount: Path, tmp_path: Path):
    _mounted(runner, mount)
    _seed(runner, mount)

    target = tmp_path / "clash.jsonl"
    target.write_text(
        json.dumps({"group_key": "spell", "group_ref": "fireball"}) + "\n"
    )
    result = _invoke(runner, mount, "import", str(target))
    assert result.exit_code == 1
    assert "already present" in result.stderr


def test_import_missing_file_fails(runner: CliRunner, mount: Path, tmp_path: Path):
    _mounted(runner, mount)
    result = _invoke(runner, mount, "import", str(tmp_path / "absent.jsonl"))
    assert result.exit_code == 1
    assert "No file" in result.stderr
