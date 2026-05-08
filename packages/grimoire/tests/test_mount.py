"""Tests for the mount-aware library API: Grimoire.mount/create/open/destroy.

Uses FakeEmbedder throughout so the file is offline and fast — fastembed
auto-loading is exercised by the CLI smoke tests instead.

Reopens with a custom embedder go through the private file-level helper
`_open_file` against the path resolved by `Mount.path_for(name)`. The mount-
aware `Grimoire.open()` always auto-loads `FastembedEmbedder` from the lock
row by design, so a few tests use a `fake_autoload` fixture to substitute
that auto-load path with a FakeEmbedder when the test needs to exercise the
public `open()` codepath end-to-end.
"""

import hashlib

import pytest
from grimoire import (
    DatabaseExists,
    Grimoire,
    GrimoireMismatch,
    GrimoireNotFound,
    InvalidMount,
    Mount,
    MountDestroyed,
)
from grimoire.core import _open_file
from grimoire.mount import (
    DB_FILENAME,
    MANIFEST_FILENAME,
    MODELS_DIRNAME,
    _read_manifest,
)


class FakeEmbedder:
    def __init__(self, model: str = "fake-v1", dimension: int = 8) -> None:
        self._model = model
        self._dimension = dimension

    @property
    def model(self) -> str:
        return self._model

    @property
    def dimension(self) -> int:
        return self._dimension

    def embed(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode()).digest()
        return [(b - 128) / 128.0 for b in digest[: self._dimension]]

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        return [self.embed(t) for t in texts]


@pytest.fixture
def fake_autoload(monkeypatch):
    """Make `Grimoire.open()` auto-load a FakeEmbedder instead of fastembed.

    Use only for tests that specifically exercise the public `open()` codepath
    end-to-end. Most reopen tests should go through `_open_file` directly via
    `Mount.path_for(name)`.
    """
    monkeypatch.setattr(
        "grimoire.core._autoload_embedder",
        lambda db, mount: FakeEmbedder(),
    )


# ---------- Grimoire.mount ----------


def test_mount_returns_handle_with_resolved_path(tmp_path):
    handle = Grimoire.mount(tmp_path)
    assert isinstance(handle, Mount)
    assert handle.path == tmp_path


def test_mount_creates_root_and_models_dir(tmp_path):
    fresh = tmp_path / "fresh"
    Grimoire.mount(fresh)
    assert fresh.exists()
    assert (fresh / MODELS_DIRNAME).is_dir()


def test_mount_does_not_write_manifest_eagerly(tmp_path):
    """Manifest is lazy — written only on first named-DB create."""
    Grimoire.mount(tmp_path)
    assert not (tmp_path / MANIFEST_FILENAME).exists()


def test_mount_uses_env_var_when_no_arg(tmp_path, monkeypatch):
    monkeypatch.setenv("GRIMOIRE_MOUNT", str(tmp_path))
    handle = Grimoire.mount()
    assert handle.path == tmp_path


def test_mount_arg_beats_env_var(tmp_path, monkeypatch):
    monkeypatch.setenv("GRIMOIRE_MOUNT", str(tmp_path / "env"))
    handle = Grimoire.mount(tmp_path / "explicit")
    assert handle.path == tmp_path / "explicit"


def test_mount_default_is_home_grimoire(tmp_path, monkeypatch):
    monkeypatch.delenv("GRIMOIRE_MOUNT", raising=False)
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    from grimoire.mount import _resolve_mount

    assert _resolve_mount(None) == tmp_path / ".grimoire"


# ---------- Grimoire.create ----------


def test_create_default_db(tmp_path):
    g = Grimoire.create(mount=tmp_path, embedder=FakeEmbedder())
    g.add(vector_text="hello")
    g.close()
    assert (tmp_path / DB_FILENAME).exists()


def test_create_named_db_lives_in_subdir(tmp_path):
    Grimoire.create("alpha", mount=tmp_path, embedder=FakeEmbedder()).close()
    assert (tmp_path / "alpha" / DB_FILENAME).exists()
    # Default DB was NOT created.
    assert not (tmp_path / DB_FILENAME).exists()


def test_create_named_writes_manifest_entry(tmp_path):
    Grimoire.create(
        "alpha",
        mount=tmp_path,
        embedder=FakeEmbedder(model="m1", dimension=8),
        description="alpha description",
    ).close()
    manifest = _read_manifest(tmp_path)
    assert "alpha" in manifest["databases"]
    entry = manifest["databases"]["alpha"]
    assert entry["model"] == "m1"
    assert entry["description"] == "alpha description"
    assert "created_at" in entry


def test_create_default_does_not_write_manifest(tmp_path):
    Grimoire.create(mount=tmp_path, embedder=FakeEmbedder()).close()
    assert not (tmp_path / MANIFEST_FILENAME).exists()


def test_create_raises_database_exists_on_collision(tmp_path):
    Grimoire.create(mount=tmp_path, embedder=FakeEmbedder()).close()
    with pytest.raises(DatabaseExists):
        Grimoire.create(mount=tmp_path, embedder=FakeEmbedder())


def test_create_raises_database_exists_on_named_collision(tmp_path):
    Grimoire.create("alpha", mount=tmp_path, embedder=FakeEmbedder()).close()
    with pytest.raises(DatabaseExists):
        Grimoire.create("alpha", mount=tmp_path, embedder=FakeEmbedder())


def test_create_rejects_reserved_name(tmp_path):
    with pytest.raises(InvalidMount):
        Grimoire.create("models", mount=tmp_path, embedder=FakeEmbedder())


def test_create_rejects_path_separator_in_name(tmp_path):
    with pytest.raises(InvalidMount):
        Grimoire.create("foo/bar", mount=tmp_path, embedder=FakeEmbedder())


def test_create_rejects_dot_prefix(tmp_path):
    with pytest.raises(InvalidMount):
        Grimoire.create(".hidden", mount=tmp_path, embedder=FakeEmbedder())


def test_create_cleans_subdir_on_failed_init(tmp_path):
    """If embedder probe fails, the named subdir should not be left behind."""

    class BoomEmbedder(FakeEmbedder):
        def embed(self, text: str) -> list[float]:
            raise RuntimeError("model fetch failed")

    with pytest.raises(RuntimeError):
        Grimoire.create("alpha", mount=tmp_path, embedder=BoomEmbedder())
    assert not (tmp_path / "alpha").exists()


# ---------- Grimoire.open ----------
#
# `Grimoire.open()` always auto-loads fastembed; tests that reopen with a
# FakeEmbedder go through `_open_file(mount.path_for(name), embedder=fake)`.
# The `fake_autoload` fixture covers the small number of tests that need to
# verify the public `open()` codepath itself.


def test_open_raises_not_found_for_missing_default(tmp_path):
    """Not-found check happens before any embedder load."""
    with pytest.raises(GrimoireNotFound):
        Grimoire.open(mount=tmp_path)


def test_open_raises_not_found_for_missing_named(tmp_path):
    Grimoire.create(mount=tmp_path, embedder=FakeEmbedder()).close()
    with pytest.raises(GrimoireNotFound):
        Grimoire.open("ghost", mount=tmp_path)


def test_open_routes_through_autoloader(tmp_path, fake_autoload):
    """Pin: `Grimoire.open()` reconstructs an embedder via `_autoload_embedder`."""
    Grimoire.create(mount=tmp_path, embedder=FakeEmbedder()).close()
    with Grimoire.open(mount=tmp_path) as g:
        g.add(vector_text="reopened via autoload")
    # The data made it.
    handle = Grimoire.mount(tmp_path)
    assert handle.peek(None).entry_count == 1


def test_open_named_routes_through_autoloader(tmp_path, fake_autoload):
    Grimoire.create("alpha", mount=tmp_path, embedder=FakeEmbedder()).close()
    with Grimoire.open("alpha", mount=tmp_path) as g:
        g.add(vector_text="hi")


def test_reopen_default_via_file_level(tmp_path):
    """Reopen with a FakeEmbedder via the private file-level helper."""
    Grimoire.create(mount=tmp_path, embedder=FakeEmbedder()).close()
    handle = Grimoire.mount(tmp_path)
    with _open_file(handle.path_for(None), embedder=FakeEmbedder()) as g:
        g.add(vector_text="reopened")
    assert handle.peek(None).entry_count == 1


def test_reopen_named_via_file_level(tmp_path):
    Grimoire.create("alpha", mount=tmp_path, embedder=FakeEmbedder()).close()
    handle = Grimoire.mount(tmp_path)
    with _open_file(handle.path_for("alpha"), embedder=FakeEmbedder()) as g:
        g.add(vector_text="reopened")


def test_open_validates_embedder_against_lock_row(tmp_path):
    """Reopen with a mismatched embedder raises — checked at the file level."""
    Grimoire.create(mount=tmp_path, embedder=FakeEmbedder(model="alpha")).close()
    handle = Grimoire.mount(tmp_path)
    with pytest.raises(GrimoireMismatch):
        _open_file(handle.path_for(None), embedder=FakeEmbedder(model="beta"))


# ---------- Grimoire.destroy ----------


def test_destroy_default_db(tmp_path):
    Grimoire.create(mount=tmp_path, embedder=FakeEmbedder()).close()
    Grimoire.destroy(mount=tmp_path)
    assert not (tmp_path / DB_FILENAME).exists()
    # Mount root and models dir survive.
    assert tmp_path.exists()
    assert (tmp_path / MODELS_DIRNAME).exists()


def test_destroy_named_db_removes_subdir_and_manifest_entry(tmp_path):
    Grimoire.create("alpha", mount=tmp_path, embedder=FakeEmbedder()).close()
    Grimoire.create("beta", mount=tmp_path, embedder=FakeEmbedder()).close()

    Grimoire.destroy("alpha", mount=tmp_path)
    assert not (tmp_path / "alpha").exists()
    manifest = _read_manifest(tmp_path)
    assert "alpha" not in manifest["databases"]
    # Sibling untouched.
    assert (tmp_path / "beta" / DB_FILENAME).exists()
    assert "beta" in manifest["databases"]


def test_destroy_missing_db_is_noop(tmp_path):
    """Idempotent: missing files and manifest entries are tolerated."""
    Grimoire.mount(tmp_path)
    Grimoire.destroy("ghost", mount=tmp_path)  # no-op, no error
    Grimoire.destroy(mount=tmp_path)  # no-op, no error


# ---------- Mount.list / peek / has / path_for ----------


def test_mount_list_walks_default_first_then_named_alphabetical(tmp_path):
    Grimoire.create(mount=tmp_path, embedder=FakeEmbedder()).close()
    Grimoire.create("zebra", mount=tmp_path, embedder=FakeEmbedder()).close()
    Grimoire.create("alpha", mount=tmp_path, embedder=FakeEmbedder()).close()

    handle = Grimoire.mount(tmp_path)
    rows = handle.list()
    names = [r.name for r in rows]
    assert names == [None, "alpha", "zebra"]
    assert rows[0].is_default is True
    assert all(r.is_default is False for r in rows[1:])


def test_mount_list_skips_manifest_entries_whose_files_are_gone(tmp_path):
    """list() reflects on-disk state; stale manifest entries are filtered."""
    Grimoire.create("alpha", mount=tmp_path, embedder=FakeEmbedder()).close()
    (tmp_path / "alpha" / DB_FILENAME).unlink()
    handle = Grimoire.mount(tmp_path)
    assert handle.list() == []


def test_mount_has_reports_existence(tmp_path):
    handle = Grimoire.mount(tmp_path)
    assert handle.has(None) is False
    assert handle.has("alpha") is False
    Grimoire.create(mount=tmp_path, embedder=FakeEmbedder()).close()
    assert handle.has(None) is True
    Grimoire.create("alpha", mount=tmp_path, embedder=FakeEmbedder()).close()
    assert handle.has("alpha") is True


def test_mount_peek_returns_stats(tmp_path):
    Grimoire.create("alpha", mount=tmp_path, embedder=FakeEmbedder()).close()
    handle = Grimoire.mount(tmp_path)
    stats = handle.peek("alpha")
    assert stats is not None
    assert stats.model == "fake-v1"
    assert stats.dimension == 8


def test_mount_peek_returns_none_for_missing(tmp_path):
    handle = Grimoire.mount(tmp_path)
    assert handle.peek("ghost") is None
    assert handle.peek(None) is None


def test_mount_path_for_resolves_paths(tmp_path):
    handle = Grimoire.mount(tmp_path)
    assert handle.path_for(None) == tmp_path / DB_FILENAME
    assert handle.path_for("alpha") == tmp_path / "alpha" / DB_FILENAME


def test_mount_path_for_validates_name(tmp_path):
    handle = Grimoire.mount(tmp_path)
    with pytest.raises(InvalidMount):
        handle.path_for("foo/bar")


# ---------- Mount.destroy ----------


def test_mount_destroy_wipes_directory(tmp_path):
    Grimoire.create(mount=tmp_path, embedder=FakeEmbedder()).close()
    Grimoire.create("alpha", mount=tmp_path, embedder=FakeEmbedder()).close()

    handle = Grimoire.mount(tmp_path)
    handle.destroy()
    assert not tmp_path.exists()


def test_mount_handle_unusable_after_destroy(tmp_path):
    handle = Grimoire.mount(tmp_path)
    handle.destroy()
    with pytest.raises(MountDestroyed):
        handle.list()
    with pytest.raises(MountDestroyed):
        handle.peek(None)
    with pytest.raises(MountDestroyed):
        _ = handle.path
    with pytest.raises(MountDestroyed):
        handle.path_for(None)


def test_mount_destroy_idempotent_on_missing_path(tmp_path):
    handle = Grimoire.mount(tmp_path / "fresh")
    import shutil

    shutil.rmtree(tmp_path / "fresh")
    handle.destroy()
    with pytest.raises(MountDestroyed):
        handle.list()


# ---------- manifest atomicity ----------


def test_manifest_write_is_atomic(tmp_path):
    """tmp+rename: a partial-write doesn't corrupt the manifest."""
    Grimoire.create("alpha", mount=tmp_path, embedder=FakeEmbedder()).close()
    manifest_path = tmp_path / MANIFEST_FILENAME
    original = manifest_path.read_bytes()

    # Simulate a half-written .tmp by leaving garbage; the next legitimate
    # write should still succeed and produce a clean file (the .tmp gets
    # overwritten before the rename).
    (tmp_path / (MANIFEST_FILENAME + ".tmp")).write_bytes(b"garbage")
    Grimoire.create("beta", mount=tmp_path, embedder=FakeEmbedder()).close()
    new = manifest_path.read_bytes()
    assert new != original
    assert b"alpha" in new and b"beta" in new


# ---------- end-to-end ----------


def test_default_and_named_dbs_are_independent(tmp_path):
    """Adds in one DB are invisible in another, even with identical group_keys."""
    with Grimoire.create(mount=tmp_path, embedder=FakeEmbedder()) as default:
        default.add(group_key="note", vector_text="default-only")

    with Grimoire.create("named", mount=tmp_path, embedder=FakeEmbedder()) as named:
        named.add(group_key="note", vector_text="named-only")

    handle = Grimoire.mount(tmp_path)
    with _open_file(handle.path_for(None), embedder=FakeEmbedder()) as default:
        contents = {e.vector_text for e in default.list()}
        assert contents == {"default-only"}

    with _open_file(handle.path_for("named"), embedder=FakeEmbedder()) as named:
        contents = {e.vector_text for e in named.list()}
        assert contents == {"named-only"}
