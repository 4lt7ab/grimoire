"""Tests for the mount-aware library API: Mount and Grimoire(...) constructor.

Uses FakeEmbedder throughout so the file is offline and fast — fastembed
auto-loading is exercised by the CLI smoke tests instead.

Most reopen tests pass an explicit FakeEmbedder via `Grimoire(name, mount=...,
embedder=fake)` — that path validates the embedder against the lock row
(raising `GrimoireMismatch` on conflict) without touching the autoloader.
The few tests that need to verify the public autoload codepath end-to-end
substitute it via the `fake_autoload` fixture.
"""

import hashlib

import pytest
from grimoire import (
    DbInfo,
    Grimoire,
    GrimoireDestroyed,
    GrimoireMismatch,
    GrimoireNotFound,
    InvalidMount,
    Mount,
    MountDestroyed,
    MountNotFound,
)
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
    """Make the autoload-on-attach path return a FakeEmbedder instead of fastembed.

    Use only for tests that specifically exercise the public attach codepath
    end-to-end (i.e., `Grimoire(name, mount=...)` with no explicit embedder).
    Most reopen tests should pass `embedder=FakeEmbedder()` directly.
    """
    monkeypatch.setattr(
        "grimoire.core._autoload_embedder",
        lambda db, mount: FakeEmbedder(),
    )


# ---------- Mount() construction ----------


def test_mount_attach_raises_on_missing_path(tmp_path):
    with pytest.raises(MountNotFound):
        Mount(tmp_path / "ghost")


def test_mount_attach_succeeds_on_existing_dir(tmp_path):
    mount = Mount(tmp_path)
    assert mount.path == tmp_path


def test_mount_create_materializes_root_and_models_dir(tmp_path):
    fresh = tmp_path / "fresh"
    Mount(fresh, create=True)
    assert fresh.exists()
    assert (fresh / MODELS_DIRNAME).is_dir()


def test_mount_create_is_idempotent(tmp_path):
    Mount(tmp_path, create=True)
    # Second call against an existing mount is a no-op.
    Mount(tmp_path, create=True)
    assert (tmp_path / MODELS_DIRNAME).is_dir()


def test_mount_create_does_not_write_manifest_eagerly(tmp_path):
    """Manifest is lazy — written only on first named-DB create."""
    Mount(tmp_path, create=True)
    assert not (tmp_path / MANIFEST_FILENAME).exists()


def test_mount_attach_rejects_non_directory_path(tmp_path):
    f = tmp_path / "file.txt"
    f.write_text("not a dir")
    with pytest.raises(InvalidMount):
        Mount(f)


def test_mount_resolve_uses_env_var_when_no_arg(tmp_path, monkeypatch):
    monkeypatch.setenv("GRIMOIRE_MOUNT", str(tmp_path))
    assert Mount.resolve() == tmp_path


def test_mount_resolve_arg_beats_env_var(tmp_path, monkeypatch):
    monkeypatch.setenv("GRIMOIRE_MOUNT", str(tmp_path / "env"))
    assert Mount.resolve(tmp_path / "explicit") == tmp_path / "explicit"


def test_mount_resolve_default_is_home_grimoire(tmp_path, monkeypatch):
    monkeypatch.delenv("GRIMOIRE_MOUNT", raising=False)
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    assert Mount.resolve() == tmp_path / ".grimoire"


def test_mount_resolve_unwraps_existing_handle(tmp_path):
    mount = Mount(tmp_path, create=True)
    assert Mount.resolve(mount) == tmp_path


def test_mount_models_path(tmp_path):
    mount = Mount(tmp_path, create=True)
    assert mount.models_path == tmp_path / MODELS_DIRNAME


# ---------- Grimoire(...) constructor: create branch ----------


def test_create_default_db(tmp_path):
    g = Grimoire(mount=tmp_path, embedder=FakeEmbedder())
    g.add(vector_text="hello")
    g.close()
    assert (tmp_path / DB_FILENAME).exists()


def test_create_named_db_lives_in_subdir(tmp_path):
    Grimoire("alpha", mount=tmp_path, embedder=FakeEmbedder()).close()
    assert (tmp_path / "alpha" / DB_FILENAME).exists()
    # Default DB was NOT created.
    assert not (tmp_path / DB_FILENAME).exists()


def test_create_named_writes_manifest_entry(tmp_path):
    Grimoire(
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
    Grimoire(mount=tmp_path, embedder=FakeEmbedder()).close()
    assert not (tmp_path / MANIFEST_FILENAME).exists()


def test_create_lazily_materializes_mount_root(tmp_path):
    fresh = tmp_path / "fresh"
    # Mount dir doesn't exist; constructor should materialize it on the create
    # branch since an embedder was supplied.
    Grimoire(mount=fresh, embedder=FakeEmbedder()).close()
    assert fresh.exists()
    assert (fresh / MODELS_DIRNAME).is_dir()


def test_create_accepts_mount_handle(tmp_path):
    mount = Mount(tmp_path, create=True)
    Grimoire("alpha", mount=mount, embedder=FakeEmbedder()).close()
    assert mount.has("alpha")


def test_create_rejects_reserved_name(tmp_path):
    with pytest.raises(InvalidMount):
        Grimoire("models", mount=tmp_path, embedder=FakeEmbedder())


def test_create_rejects_path_separator_in_name(tmp_path):
    with pytest.raises(InvalidMount):
        Grimoire("foo/bar", mount=tmp_path, embedder=FakeEmbedder())


def test_create_rejects_dot_prefix(tmp_path):
    with pytest.raises(InvalidMount):
        Grimoire(".hidden", mount=tmp_path, embedder=FakeEmbedder())


def test_create_cleans_subdir_on_failed_init(tmp_path):
    """If embedder probe fails, the named subdir should not be left behind."""

    class BoomEmbedder(FakeEmbedder):
        def embed(self, text: str) -> list[float]:
            raise RuntimeError("model fetch failed")

    with pytest.raises(RuntimeError):
        Grimoire("alpha", mount=tmp_path, embedder=BoomEmbedder())
    assert not (tmp_path / "alpha").exists()


# ---------- Grimoire(...) constructor: attach branch ----------


def test_attach_raises_not_found_for_missing_default(tmp_path):
    """Missing DB without an embedder consent signal raises."""
    with pytest.raises(GrimoireNotFound):
        Grimoire(mount=tmp_path)


def test_attach_raises_not_found_for_missing_named(tmp_path):
    Grimoire(mount=tmp_path, embedder=FakeEmbedder()).close()
    with pytest.raises(GrimoireNotFound):
        Grimoire("ghost", mount=tmp_path)


def test_attach_via_explicit_embedder_validates_lock_row(tmp_path):
    """Reopen with the same embedder model attaches; mismatched model raises."""
    Grimoire(mount=tmp_path, embedder=FakeEmbedder(model="alpha")).close()
    with Grimoire(mount=tmp_path, embedder=FakeEmbedder(model="alpha")) as g:
        g.add(vector_text="reopened")
    with pytest.raises(GrimoireMismatch):
        Grimoire(mount=tmp_path, embedder=FakeEmbedder(model="beta"))


def test_attach_routes_through_autoloader(tmp_path, fake_autoload):
    """Pin: an embedder-less attach reconstructs via `_autoload_embedder`."""
    Grimoire(mount=tmp_path, embedder=FakeEmbedder()).close()
    with Grimoire(mount=tmp_path) as g:
        g.add(vector_text="reopened via autoload")
    mount = Mount(tmp_path)
    assert mount.peek().entry_count == 1


def test_attach_named_routes_through_autoloader(tmp_path, fake_autoload):
    Grimoire("alpha", mount=tmp_path, embedder=FakeEmbedder()).close()
    with Grimoire("alpha", mount=tmp_path) as g:
        g.add(vector_text="hi")


def test_create_or_attach_idempotent(tmp_path):
    """Embedder consent + missing DB creates; embedder consent + present DB attaches."""
    # First call creates.
    Grimoire("alpha", mount=tmp_path, embedder=FakeEmbedder()).close()
    # Second call attaches to the existing DB (no DatabaseExists).
    with Grimoire("alpha", mount=tmp_path, embedder=FakeEmbedder()) as g:
        g.add(vector_text="post-attach")


def test_description_silently_ignored_on_existing_db(tmp_path):
    """Manifest description is stamped at creation only; reattach doesn't update."""
    Grimoire(
        "alpha", mount=tmp_path, embedder=FakeEmbedder(), description="original"
    ).close()
    Grimoire(
        "alpha", mount=tmp_path, embedder=FakeEmbedder(), description="overridden"
    ).close()
    manifest = _read_manifest(tmp_path)
    assert manifest["databases"]["alpha"]["description"] == "original"


# ---------- Grimoire.destroy ----------


def test_grimoire_destroy_removes_default_db(tmp_path):
    g = Grimoire(mount=tmp_path, embedder=FakeEmbedder())
    g.destroy()
    assert not (tmp_path / DB_FILENAME).exists()
    # Mount root and models dir survive.
    assert tmp_path.exists()
    assert (tmp_path / MODELS_DIRNAME).exists()


def test_grimoire_destroy_removes_named_db_and_manifest_entry(tmp_path):
    Grimoire("alpha", mount=tmp_path, embedder=FakeEmbedder()).close()
    Grimoire("beta", mount=tmp_path, embedder=FakeEmbedder()).close()

    g = Grimoire("alpha", mount=tmp_path, embedder=FakeEmbedder())
    g.destroy()
    assert not (tmp_path / "alpha").exists()
    manifest = _read_manifest(tmp_path)
    assert "alpha" not in manifest["databases"]
    # Sibling untouched.
    assert (tmp_path / "beta" / DB_FILENAME).exists()
    assert "beta" in manifest["databases"]


def test_grimoire_handle_unusable_after_destroy(tmp_path):
    g = Grimoire(mount=tmp_path, embedder=FakeEmbedder())
    g.destroy()
    with pytest.raises(GrimoireDestroyed):
        g.add(vector_text="post-destroy")
    with pytest.raises(GrimoireDestroyed):
        g.list()
    with pytest.raises(GrimoireDestroyed):
        g.destroy()


# ---------- Mount.drop ----------


def test_mount_drop_removes_default_db(tmp_path):
    Grimoire(mount=tmp_path, embedder=FakeEmbedder()).close()
    Mount(tmp_path).drop()
    assert not (tmp_path / DB_FILENAME).exists()
    assert tmp_path.exists()


def test_mount_drop_removes_named_db_and_manifest_entry(tmp_path):
    Grimoire("alpha", mount=tmp_path, embedder=FakeEmbedder()).close()
    Grimoire("beta", mount=tmp_path, embedder=FakeEmbedder()).close()

    Mount(tmp_path).drop("alpha")
    assert not (tmp_path / "alpha").exists()
    manifest = _read_manifest(tmp_path)
    assert "alpha" not in manifest["databases"]
    # Sibling untouched.
    assert (tmp_path / "beta" / DB_FILENAME).exists()
    assert "beta" in manifest["databases"]


def test_mount_drop_missing_db_is_noop(tmp_path):
    """Idempotent: missing files and manifest entries are tolerated."""
    mount = Mount(tmp_path, create=True)
    mount.drop("ghost")  # no error
    mount.drop()  # no error


# ---------- Mount.list / peek / has / path_for ----------


def test_mount_list_walks_default_first_then_named_alphabetical(tmp_path):
    Grimoire(mount=tmp_path, embedder=FakeEmbedder()).close()
    Grimoire("zebra", mount=tmp_path, embedder=FakeEmbedder()).close()
    Grimoire("alpha", mount=tmp_path, embedder=FakeEmbedder()).close()

    rows = Mount(tmp_path).list()
    names = [r.name for r in rows]
    assert names == [None, "alpha", "zebra"]
    assert rows[0].is_default is True
    assert all(r.is_default is False for r in rows[1:])


def test_mount_list_skips_manifest_entries_whose_files_are_gone(tmp_path):
    """list() reflects on-disk state; stale manifest entries are filtered."""
    Grimoire("alpha", mount=tmp_path, embedder=FakeEmbedder()).close()
    (tmp_path / "alpha" / DB_FILENAME).unlink()
    assert Mount(tmp_path).list() == []


def test_mount_list_returns_db_info_shape(tmp_path):
    Grimoire(
        "alpha", mount=tmp_path, embedder=FakeEmbedder(model="m1", dimension=8)
    ).close()
    [info] = Mount(tmp_path).list()
    assert isinstance(info, DbInfo)
    assert info.name == "alpha"
    assert info.model == "m1"
    assert info.dimension == 8


def test_mount_has_reports_existence(tmp_path):
    mount = Mount(tmp_path, create=True)
    assert mount.has() is False
    assert mount.has("alpha") is False
    Grimoire(mount=tmp_path, embedder=FakeEmbedder()).close()
    assert mount.has() is True
    Grimoire("alpha", mount=tmp_path, embedder=FakeEmbedder()).close()
    assert mount.has("alpha") is True


def test_mount_peek_returns_stats(tmp_path):
    Grimoire("alpha", mount=tmp_path, embedder=FakeEmbedder()).close()
    stats = Mount(tmp_path).peek("alpha")
    assert stats is not None
    assert stats.model == "fake-v1"
    assert stats.dimension == 8


def test_mount_peek_returns_none_for_missing(tmp_path):
    mount = Mount(tmp_path, create=True)
    assert mount.peek("ghost") is None
    assert mount.peek() is None


def test_mount_path_for_resolves_paths(tmp_path):
    mount = Mount(tmp_path, create=True)
    assert mount.path_for() == tmp_path / DB_FILENAME
    assert mount.path_for("alpha") == tmp_path / "alpha" / DB_FILENAME


def test_mount_path_for_validates_name(tmp_path):
    mount = Mount(tmp_path, create=True)
    with pytest.raises(InvalidMount):
        mount.path_for("foo/bar")


# ---------- Mount.destroy ----------


def test_mount_destroy_wipes_directory(tmp_path):
    Grimoire(mount=tmp_path, embedder=FakeEmbedder()).close()
    Grimoire("alpha", mount=tmp_path, embedder=FakeEmbedder()).close()

    mount = Mount(tmp_path)
    mount.destroy()
    assert not tmp_path.exists()


def test_mount_handle_unusable_after_destroy(tmp_path):
    mount = Mount(tmp_path, create=True)
    mount.destroy()
    with pytest.raises(MountDestroyed):
        mount.list()
    with pytest.raises(MountDestroyed):
        mount.peek()
    with pytest.raises(MountDestroyed):
        _ = mount.path
    with pytest.raises(MountDestroyed):
        _ = mount.models_path
    with pytest.raises(MountDestroyed):
        mount.path_for()
    with pytest.raises(MountDestroyed):
        mount.drop("alpha")


def test_mount_destroy_idempotent_on_missing_path(tmp_path):
    mount = Mount(tmp_path / "fresh", create=True)
    import shutil

    shutil.rmtree(tmp_path / "fresh")
    mount.destroy()
    with pytest.raises(MountDestroyed):
        mount.list()


# ---------- manifest atomicity ----------


def test_manifest_write_is_atomic(tmp_path):
    """tmp+rename: a partial-write doesn't corrupt the manifest."""
    Grimoire("alpha", mount=tmp_path, embedder=FakeEmbedder()).close()
    manifest_path = tmp_path / MANIFEST_FILENAME
    original = manifest_path.read_bytes()

    # Simulate a half-written .tmp by leaving garbage; the next legitimate
    # write should still succeed and produce a clean file (the .tmp gets
    # overwritten before the rename).
    (tmp_path / (MANIFEST_FILENAME + ".tmp")).write_bytes(b"garbage")
    Grimoire("beta", mount=tmp_path, embedder=FakeEmbedder()).close()
    new = manifest_path.read_bytes()
    assert new != original
    assert b"alpha" in new and b"beta" in new


# ---------- end-to-end ----------


def test_default_and_named_dbs_are_independent(tmp_path):
    """Adds in one DB are invisible in another, even with identical group_keys."""
    with Grimoire(mount=tmp_path, embedder=FakeEmbedder()) as default:
        default.add(group_key="note", vector_text="default-only")

    with Grimoire("named", mount=tmp_path, embedder=FakeEmbedder()) as named:
        named.add(group_key="note", vector_text="named-only")

    with Grimoire(mount=tmp_path, embedder=FakeEmbedder()) as default:
        contents = {e.vector_text for e in default.list()}
        assert contents == {"default-only"}

    with Grimoire("named", mount=tmp_path, embedder=FakeEmbedder()) as named:
        contents = {e.vector_text for e in named.list()}
        assert contents == {"named-only"}
