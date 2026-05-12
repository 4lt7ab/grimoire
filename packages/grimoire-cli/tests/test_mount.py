from pathlib import Path

from grimoire_cli import mount as mount_mod
from grimoire_cli.mount import (
    DB_FILENAME,
    DEFAULT_MOUNT,
    ENV_VAR,
    MODELS_DIRNAME,
    REGISTRY_FILENAME,
    Mount,
    create,
    resolve,
)


def test_mount_path_accessors(tmp_path):
    m = Mount(tmp_path)
    assert m.registry_path == tmp_path / REGISTRY_FILENAME
    assert m.models_dir == tmp_path / MODELS_DIRNAME
    assert m.default_db == tmp_path / DB_FILENAME


def test_db_path_default(tmp_path):
    assert Mount(tmp_path).db_path(None) == tmp_path / DB_FILENAME


def test_db_path_named(tmp_path):
    assert Mount(tmp_path).db_path("foo") == tmp_path / "foo" / DB_FILENAME


def test_resolve_explicit_wins(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_VAR, str(tmp_path / "env-mount"))
    explicit = tmp_path / "flag-mount"
    assert resolve(explicit).path == explicit.resolve()


def test_resolve_env_when_no_explicit(tmp_path, monkeypatch):
    target = tmp_path / "env-mount"
    monkeypatch.setenv(ENV_VAR, str(target))
    assert resolve().path == target.resolve()


def test_resolve_default_when_unset(monkeypatch):
    monkeypatch.delenv(ENV_VAR, raising=False)
    assert resolve().path == DEFAULT_MOUNT.resolve()


def test_resolve_expands_user_in_env(monkeypatch):
    monkeypatch.setenv(ENV_VAR, "~/somewhere")
    expected = (Path.home() / "somewhere").resolve()
    assert resolve().path == expected


def test_ensure_layout_creates_everything(tmp_path):
    m = Mount(tmp_path / "fresh")
    create(m)
    assert m.path.is_dir()
    assert m.models_dir.is_dir()
    assert m.registry_path.is_file()


def test_ensure_layout_is_idempotent(tmp_path):
    m = Mount(tmp_path / "fresh")
    create(m)
    m.registry_path.write_text("# pre-existing\n")
    create(m)
    assert m.registry_path.read_text() == "# pre-existing\n"


def test_module_constants_match_documented_layout():
    # Guardrail: the CLAUDE.md mount layout depends on these names.
    assert mount_mod.DB_FILENAME == "grimoire.db"
    assert mount_mod.REGISTRY_FILENAME == "grimoire.toml"
    assert mount_mod.MODELS_DIRNAME == "models"
    assert mount_mod.ENV_VAR == "GRIMOIRE_MOUNT"
    assert mount_mod.DEFAULT_MOUNT == Path.home() / ".grimoire"
