import json
from pathlib import Path

from grimoire_cli import manifest
from grimoire_cli.main import app
from typer.testing import CliRunner


def _invoke(runner: CliRunner, mount: Path, *args: str):
    return runner.invoke(app, ["--mount", str(mount), *args])


def test_mount_creates_layout(runner: CliRunner, mount: Path):
    result = _invoke(runner, mount)  # bare grimoire pre-init should fail
    assert result.exit_code == 1
    assert "No mount" in result.stderr

    result = _invoke(runner, mount, "mount")
    assert result.exit_code == 0, result.stderr
    assert (mount / "grimoire.toml").exists()
    assert (mount / "models").is_dir()
    assert (mount / "grimoire.db").exists()

    # Manifest tracks only named DBs — default DB does not appear.
    assert manifest.read(mount) == {}


def test_mount_is_idempotent(runner: CliRunner, mount: Path):
    first = _invoke(runner, mount, "mount")
    assert first.exit_code == 0
    second = _invoke(runner, mount, "mount")
    assert second.exit_code == 0
    assert "already initialized" in second.stdout


def test_bare_grimoire_reports_peek(runner: CliRunner, mount: Path):
    _invoke(runner, mount, "mount")
    result = _invoke(runner, mount)
    assert result.exit_code == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["mount"] == str(mount.resolve())
    assert payload["db"] is None
    assert payload["model"] == "noop"
    assert payload["entry_count"] == 0


def test_mount_destroy_requires_yes(runner: CliRunner, mount: Path):
    _invoke(runner, mount, "mount")
    result = _invoke(runner, mount, "mount", "destroy")
    assert result.exit_code == 1
    assert mount.exists()


def test_mount_destroy_wipes_directory(runner: CliRunner, mount: Path):
    _invoke(runner, mount, "mount")
    result = _invoke(runner, mount, "mount", "destroy", "--yes")
    assert result.exit_code == 0, result.stderr
    assert not mount.exists()


def test_mount_via_env_var(runner: CliRunner, mount: Path):
    result = runner.invoke(app, ["mount"], env={"GRIMOIRE_MOUNT": str(mount)})
    assert result.exit_code == 0, result.stderr
    assert (mount / "grimoire.db").exists()
