from pathlib import Path

from grimoire_cli import mount as mount_mod
from grimoire_cli.mount import DEFAULT_MOUNT, ENV_VAR, resolve


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
    assert resolve().path == DEFAULT_MOUNT


def test_resolve_expands_user_in_env(monkeypatch):
    monkeypatch.setenv(ENV_VAR, "~/somewhere")
    expected = (Path.home() / "somewhere").resolve()
    assert resolve().path == expected


def test_module_constants_match_documented_layout():
    # Guardrail: the CLAUDE.md mount layout depends on these names.
    assert mount_mod.DB_FILENAME == "grimoire.db"
    assert mount_mod.REGISTRY_FILENAME == "grimoire.toml"
    assert mount_mod.MODELS_DIRNAME == "__models__"
    assert mount_mod.ENV_VAR == "GRIMOIRE_MOUNT"
    assert mount_mod.DEFAULT_MOUNT == Path.home() / ".grimoire"
