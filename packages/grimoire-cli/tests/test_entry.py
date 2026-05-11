import json
from pathlib import Path

from grimoire_cli.main import app
from typer.testing import CliRunner


def _invoke(runner: CliRunner, mount: Path, *args: str):
    return runner.invoke(app, ["--mount", str(mount), *args])


def _mounted(runner: CliRunner, mount: Path):
    result = _invoke(runner, mount, "mount")
    assert result.exit_code == 0, result.stderr


def test_entry_add_requires_mount(runner: CliRunner, mount: Path):
    result = _invoke(runner, mount, "entry", "add")
    assert result.exit_code == 1
    assert "No mount" in result.stderr


def test_entry_add_emits_id(runner: CliRunner, mount: Path):
    _mounted(runner, mount)
    result = _invoke(
        runner,
        mount,
        "entry",
        "add",
        "--group-key",
        "spell",
        "--payload",
        json.dumps({"name": "fireball"}),
    )
    assert result.exit_code == 0, result.stderr
    saved = json.loads(result.stdout)
    assert saved["group_key"] == "spell"
    assert saved["payload"] == {"name": "fireball"}
    assert saved["id"]


def test_entry_fetch_returns_payload(runner: CliRunner, mount: Path):
    _mounted(runner, mount)
    add = _invoke(
        runner,
        mount,
        "entry",
        "add",
        "--group-key",
        "spell",
        "--payload",
        json.dumps({"name": "icebolt"}),
    )
    saved_id = json.loads(add.stdout)["id"]

    fetch = _invoke(runner, mount, "entry", "fetch", "--id", saved_id)
    assert fetch.exit_code == 0, fetch.stderr
    rows = [json.loads(line) for line in fetch.stdout.strip().splitlines()]
    assert len(rows) == 1
    assert rows[0]["payload"] == {"name": "icebolt"}


def test_entry_remove_returns_id(runner: CliRunner, mount: Path):
    _mounted(runner, mount)
    add = _invoke(runner, mount, "entry", "add")
    saved_id = json.loads(add.stdout)["id"]

    remove = _invoke(runner, mount, "entry", "remove", saved_id)
    assert remove.exit_code == 0, remove.stderr
    assert remove.stdout.strip() == saved_id

    after = _invoke(runner, mount, "entry", "fetch", "--id", saved_id)
    assert after.stdout.strip() == ""
