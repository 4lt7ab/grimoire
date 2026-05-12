from pathlib import Path

import pytest

from grimoire import mount as mount_mod
from grimoire.mount import (
    DB_FILENAME,
    DEFAULT_MOUNT,
    MODELS_DIRNAME,
    REGISTRY_FILENAME,
    Mount,
    create,
    destroy,
)


def test_default_mount_is_home_grimoire():
    assert Path.home() / ".grimoire" == DEFAULT_MOUNT


def test_mount_defaults_to_default_mount():
    assert Mount().path == DEFAULT_MOUNT


def test_mount_accepts_explicit_path(tmp_path):
    assert Mount(tmp_path).path == tmp_path


def test_path_accessors(tmp_path):
    m = Mount(tmp_path)
    assert m.registry_path == tmp_path / REGISTRY_FILENAME
    assert m.models_dir == tmp_path / MODELS_DIRNAME
    assert m.default_db == tmp_path / DB_FILENAME


def test_db_path_default(tmp_path):
    assert Mount(tmp_path).db_path(None) == tmp_path / DB_FILENAME


def test_db_path_named(tmp_path):
    assert Mount(tmp_path).db_path("spellbook") == tmp_path / "spellbook" / DB_FILENAME


def test_db_path_lowercases_name(tmp_path):
    expected = tmp_path / "spellbook" / DB_FILENAME
    assert Mount(tmp_path).db_path("Spellbook") == expected
    assert Mount(tmp_path).db_path("SPELLBOOK") == expected


def test_db_path_accepts_hyphens_and_underscores(tmp_path):
    assert Mount(tmp_path).db_path("my_db-1") == tmp_path / "my_db-1" / DB_FILENAME


@pytest.mark.parametrize(
    "bad",
    ["", "spell book", "subdir/db", "a.b", "spell!", "spell+book", "café"],
)
def test_db_path_rejects_invalid_names(tmp_path, bad):
    with pytest.raises(ValueError, match="Invalid database name"):
        Mount(tmp_path).db_path(bad)


def test_exists_is_false_for_missing_path(tmp_path):
    assert Mount(tmp_path / "nope").exists() is False


def test_exists_is_false_when_components_missing(tmp_path):
    m = Mount(tmp_path)
    m.path.mkdir(exist_ok=True)
    assert m.exists() is False
    m.models_dir.mkdir()
    assert m.exists() is False
    m.registry_path.touch()
    assert m.exists() is False
    m.default_db.touch()
    assert m.exists() is True


def test_create_initializes_layout(tmp_path):
    m = Mount(tmp_path / "fresh")
    create(m)
    assert m.path.is_dir()
    assert m.models_dir.is_dir()
    assert m.registry_path.is_file()
    assert m.default_db.is_file()
    assert m.exists()


def test_create_creates_parents(tmp_path):
    m = Mount(tmp_path / "nested" / "path" / "mount")
    create(m)
    assert m.exists()


def test_create_is_idempotent(tmp_path):
    m = Mount(tmp_path / "fresh")
    create(m)
    m.registry_path.write_text("# pre-existing\n")
    create(m)
    assert m.registry_path.read_text() == "# pre-existing\n"


def test_destroy_removes_mount(tmp_path):
    m = Mount(tmp_path / "doomed")
    create(m)
    destroy(m)
    assert not m.path.exists()


def test_module_constants_match_documented_layout():
    # Guardrail: the CLAUDE.md mount layout depends on these names.
    assert mount_mod.DB_FILENAME == "grimoire.db"
    assert mount_mod.REGISTRY_FILENAME == "grimoire.toml"
    assert mount_mod.MODELS_DIRNAME == "__models__"
    assert Path.home() / ".grimoire" == mount_mod.DEFAULT_MOUNT
