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


def test_entry_get_returns_payload(runner: CliRunner, mount: Path):
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

    got = _invoke(runner, mount, "entry", "get", saved_id)
    assert got.exit_code == 0, got.stderr
    row = json.loads(got.stdout)
    assert row["payload"] == {"name": "icebolt"}
    assert row["id"] == saved_id


def test_entry_get_missing_fails(runner: CliRunner, mount: Path):
    _mounted(runner, mount)
    result = _invoke(runner, mount, "entry", "get", "01MISSINGMISSINGMISSINGMI")
    assert result.exit_code == 1
    assert "No entry" in result.stderr


def test_entry_remove_returns_id(runner: CliRunner, mount: Path):
    _mounted(runner, mount)
    add = _invoke(runner, mount, "entry", "add")
    saved_id = json.loads(add.stdout)["id"]

    remove = _invoke(runner, mount, "entry", "remove", saved_id)
    assert remove.exit_code == 0, remove.stderr
    assert remove.stdout.strip() == saved_id

    after = _invoke(runner, mount, "entry", "get", saved_id)
    assert after.exit_code == 1
