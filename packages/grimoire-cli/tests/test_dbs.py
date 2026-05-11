import json
from pathlib import Path

from grimoire_cli import manifest
from grimoire_cli.main import app
from typer.testing import CliRunner


def _invoke(runner: CliRunner, mount: Path, *args: str):
    return runner.invoke(app, ["--mount", str(mount), *args])


def _mounted(runner: CliRunner, mount: Path):
    result = _invoke(runner, mount, "mount")
    assert result.exit_code == 0, result.stderr


def test_create_adds_subdir_and_manifest(runner: CliRunner, mount: Path):
    _mounted(runner, mount)
    result = _invoke(runner, mount, "create", "grimoire", "--description", "kb")
    assert result.exit_code == 0, result.stderr
    assert (mount / "grimoire" / "grimoire.db").exists()

    records = manifest.read(mount)
    assert set(records) == {"grimoire"}
    assert records["grimoire"].model == "noop"
    assert records["grimoire"].description == "kb"
    assert records["grimoire"].created_at


def test_create_rejects_existing_name(runner: CliRunner, mount: Path):
    _mounted(runner, mount)
    _invoke(runner, mount, "create", "grimoire")
    result = _invoke(runner, mount, "create", "grimoire")
    assert result.exit_code == 1
    assert "already exists" in result.stderr


def test_create_rejects_reserved_name(runner: CliRunner, mount: Path):
    _mounted(runner, mount)
    for bad in ("grimoire.db", "models", "grimoire.toml", "../escape", ""):
        result = _invoke(runner, mount, "create", bad)
        assert result.exit_code == 1, (bad, result.stdout, result.stderr)


def test_destroy_named_removes_dir_and_manifest(runner: CliRunner, mount: Path):
    _mounted(runner, mount)
    _invoke(runner, mount, "create", "grimoire")

    refuse = _invoke(runner, mount, "destroy", "grimoire")
    assert refuse.exit_code == 1
    assert (mount / "grimoire" / "grimoire.db").exists()

    result = _invoke(runner, mount, "destroy", "grimoire", "--yes")
    assert result.exit_code == 0, result.stderr
    assert not (mount / "grimoire").exists()
    assert manifest.read(mount) == {}


def test_destroy_missing_named_fails(runner: CliRunner, mount: Path):
    _mounted(runner, mount)
    result = _invoke(runner, mount, "destroy", "absent", "--yes")
    assert result.exit_code == 1
    assert "No DB" in result.stderr


def test_ls_lists_default_and_named(runner: CliRunner, mount: Path):
    _mounted(runner, mount)
    _invoke(runner, mount, "create", "grimoire", "--description", "kb")

    result = _invoke(runner, mount, "ls")
    assert result.exit_code == 0, result.stderr
    rows = [json.loads(line) for line in result.stdout.strip().splitlines()]
    assert len(rows) == 2

    [default, named] = rows
    assert default["name"] is None
    assert default["default"] is True
    assert default["entry_count"] == 0
    assert named["name"] == "grimoire"
    assert named["default"] is False
    assert named["description"] == "kb"


def test_named_db_is_addressable_via_flag(runner: CliRunner, mount: Path):
    _mounted(runner, mount)
    _invoke(runner, mount, "create", "grimoire")

    add = _invoke(
        runner,
        mount,
        "--db",
        "grimoire",
        "entry",
        "add",
        "--payload",
        json.dumps({"in": "named"}),
    )
    assert add.exit_code == 0, add.stderr

    # The default DB stays empty.
    bare = _invoke(runner, mount)
    assert json.loads(bare.stdout)["entry_count"] == 0

    bare_named = _invoke(runner, mount, "--db", "grimoire")
    assert json.loads(bare_named.stdout)["entry_count"] == 1


def test_named_db_via_env(runner: CliRunner, mount: Path):
    _mounted(runner, mount)
    _invoke(runner, mount, "create", "grimoire")

    add = runner.invoke(
        app,
        ["entry", "add", "--payload", json.dumps({"via": "env"})],
        env={"GRIMOIRE_MOUNT": str(mount), "GRIMOIRE_DB": "grimoire"},
    )
    assert add.exit_code == 0, add.stderr
