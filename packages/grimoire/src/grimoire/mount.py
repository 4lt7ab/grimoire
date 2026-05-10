"""Mount: a directory holding one or more grimoire databases plus a shared cache.

A mount layout:

    <mount>/
    ├── grimoire.toml          # registry of named DBs (lazy)
    ├── models/                # shared embedder cache
    ├── grimoire.db            # the default DB (no name, no subdir)
    ├── <name>/
    │   └── grimoire.db        # a named DB
    └── ...

The default DB lives at a fixed path by convention and has no manifest entry.
Named DBs live in per-name subdirectories and are tracked in the manifest.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import tomli_w

from grimoire.errors import (
    InvalidMount,
    MountDestroyed,
    MountNotFound,
)
from grimoire.models import Stats

DEFAULT_MOUNT_DIRNAME = ".grimoire"
MANIFEST_FILENAME = "grimoire.toml"
DB_FILENAME = "grimoire.db"
MODELS_DIRNAME = "models"
MANIFEST_SCHEMA_VERSION = 1


def _default_mount() -> Path:
    """Resolve the default mount path lazily.

    `Path.home()` is called per invocation so changes to `$HOME` (e.g. test
    monkeypatches) are reflected, instead of being baked in at import time.
    """
    return Path.home() / DEFAULT_MOUNT_DIRNAME


# Names a database cannot take, because they collide with mount-level files
# or directories that live alongside the named-DB subdirs.
_RESERVED_NAMES = frozenset({MODELS_DIRNAME, MANIFEST_FILENAME, DB_FILENAME})


@dataclass
class DbInfo:
    """Lightweight summary of a database in a mount, suitable for `ls` output."""

    name: str | None  # None for the default
    path: Path
    model: str
    dimension: int
    entry_count: int
    is_default: bool


def _resolve_mount(path: str | Path | Mount | None) -> Path:
    """Resolve a mount-shaped argument to a concrete filesystem path.

    Accepts a `Mount` handle (returns its path; triggers the destroyed-handle
    check), an explicit string/Path, or None — in which case the order is
    `GRIMOIRE_MOUNT` env var > default `~/.grimoire`.
    """
    if isinstance(path, Mount):
        return path.path
    if path is not None:
        return Path(path).expanduser()
    env = os.environ.get("GRIMOIRE_MOUNT")
    if env:
        return Path(env).expanduser()
    return _default_mount()


def _db_path(mount: Path, name: str | None) -> Path:
    """Resolve the SQLite path for a database in the mount.

    `None` resolves to the default at `<mount>/grimoire.db`.
    A name resolves to `<mount>/<name>/grimoire.db`.
    """
    if name is None:
        return mount / DB_FILENAME
    return mount / name / DB_FILENAME


def _manifest_path(mount: Path) -> Path:
    return mount / MANIFEST_FILENAME


def _read_manifest(mount: Path) -> dict:
    """Read the manifest TOML, returning an empty manifest if missing.

    Always normalizes the result so callers can rely on `manifest["databases"]`
    being a dict, regardless of whether the file existed.
    """
    p = _manifest_path(mount)
    if not p.exists():
        return {"schema_version": MANIFEST_SCHEMA_VERSION, "databases": {}}
    with p.open("rb") as f:
        data = tomllib.load(f)
    data.setdefault("schema_version", MANIFEST_SCHEMA_VERSION)
    data.setdefault("databases", {})
    return data


def _write_manifest(mount: Path, manifest: dict) -> None:
    """Atomic write — tmp + rename — so concurrent writes don't truncate."""
    p = _manifest_path(mount)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with tmp.open("wb") as f:
        tomli_w.dump(manifest, f)
    os.replace(tmp, p)


def _validate_name(name: str) -> None:
    """Reject names that would create unsafe paths or collide with mount internals."""
    if not isinstance(name, str) or not name:
        raise InvalidMount("Database name must be a non-empty string")
    if name in _RESERVED_NAMES:
        raise InvalidMount(f"Database name {name!r} is reserved")
    if name.startswith("."):
        raise InvalidMount(f"Database name {name!r} cannot start with '.'")
    if "/" in name or "\\" in name or "\x00" in name:
        raise InvalidMount(f"Database name {name!r} cannot contain path separators")


def _ensure_mount_dirs(mount: Path) -> None:
    """Create the mount root and shared models cache; safe if they exist."""
    mount.mkdir(parents=True, exist_ok=True)
    (mount / MODELS_DIRNAME).mkdir(parents=True, exist_ok=True)


def _peek_file(path: Path) -> Stats | None:
    """Read metadata + counts from a grimoire file without opening it for use.

    Returns None if the file is missing or not a grimoire database. Does not
    load sqlite-vec or require an embedder, so it is safe for inspection
    (mount listings, model auto-detect) before deciding how to open.
    """
    if not path.exists():
        return None
    try:
        conn = sqlite3.connect(path)
        try:
            row = conn.execute(
                "SELECT model, dimension FROM grimoire WHERE id = 1"
            ).fetchone()
            if row is None:
                return None
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            count = conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
            group_key_rows = conn.execute(
                "SELECT group_key, COUNT(*) FROM entries "
                "GROUP BY group_key ORDER BY group_key"
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return None
    return Stats(
        model=row[0],
        dimension=row[1],
        schema_version=version,
        entry_count=count,
        groups=dict(group_key_rows),
    )


class Mount:
    """A handle to a grimoire mount directory.

    `Mount(path)` attaches to an existing mount directory and raises
    `MountNotFound` if the path does not exist. `Mount(path, create=True)`
    materializes the mount root and the shared `models/` cache if either is
    missing — idempotent. Operations on a destroyed handle raise
    `MountDestroyed`.
    """

    @staticmethod
    def resolve(path: str | Path | Mount | None = None) -> Path:
        """Resolve a mount-shaped argument to a filesystem path without touching disk.

        Order: explicit `path` arg (Mount handle, str, or Path) > `GRIMOIRE_MOUNT`
        env var > default `~/.grimoire`. Useful when a caller needs the resolved
        path before deciding whether to construct a handle (e.g. CLIs that may
        be about to create the mount with `Mount(path, create=True)`).
        """
        return _resolve_mount(path)

    def __init__(self, path: str | Path, *, create: bool = False) -> None:
        resolved = Path(path).expanduser()
        if create:
            if resolved.exists() and not resolved.is_dir():
                raise InvalidMount(
                    f"Mount path {resolved} exists and is not a directory"
                )
            _ensure_mount_dirs(resolved)
        else:
            if not resolved.exists():
                raise MountNotFound(f"No mount directory at {resolved}")
            if not resolved.is_dir():
                raise InvalidMount(f"Mount path {resolved} is not a directory")
        self._path = resolved
        self._destroyed = False

    @property
    def path(self) -> Path:
        self._check_alive()
        return self._path

    @property
    def models_path(self) -> Path:
        """Path to the shared embedder cache directory inside the mount."""
        self._check_alive()
        return self._path / MODELS_DIRNAME

    def _check_alive(self) -> None:
        if self._destroyed:
            raise MountDestroyed(
                f"Mount at {self._path} has been destroyed; handle is unusable"
            )

    def has(self, name: str | None = None) -> bool:
        """True if a database with this name exists in the mount."""
        self._check_alive()
        if name is not None:
            _validate_name(name)
        return _db_path(self._path, name).exists()

    def path_for(self, name: str | None = None) -> Path:
        """Resolve the SQLite file path for `name` in this mount.

        `None` resolves to the default at `<mount>/grimoire.db`; a name to
        `<mount>/<name>/grimoire.db`. Useful when constructing a path to hand
        to lower-level operations.
        """
        self._check_alive()
        if name is not None:
            _validate_name(name)
        return _db_path(self._path, name)

    def peek(self, name: str | None = None) -> Stats | None:
        """Return Stats for a database without opening it. None if missing."""
        self._check_alive()
        if name is not None:
            _validate_name(name)
        return _peek_file(_db_path(self._path, name))

    def list(self) -> list[DbInfo]:
        """Return DbInfo for every database in the mount.

        Walks the default DB (if present at `<mount>/grimoire.db`) first, then
        each named DB in the manifest in alphabetical order. Manifest entries
        whose files are missing are silently skipped — the goal is reflecting
        actual on-disk state, not the manifest's wishes.
        """
        self._check_alive()
        infos: list[DbInfo] = []

        default_path = _db_path(self._path, None)
        default_stats = _peek_file(default_path)
        if default_stats is not None:
            infos.append(
                DbInfo(
                    name=None,
                    path=default_path,
                    model=default_stats.model,
                    dimension=default_stats.dimension,
                    entry_count=default_stats.entry_count,
                    is_default=True,
                )
            )

        manifest = _read_manifest(self._path)
        for name in sorted(manifest["databases"].keys()):
            db_path = _db_path(self._path, name)
            stats = _peek_file(db_path)
            if stats is None:
                continue
            infos.append(
                DbInfo(
                    name=name,
                    path=db_path,
                    model=stats.model,
                    dimension=stats.dimension,
                    entry_count=stats.entry_count,
                    is_default=False,
                )
            )
        return infos

    def drop(self, name: str | None = None) -> None:
        """Delete one database from the mount.

        `None` removes the default DB; a name removes the named DB and its
        subdirectory and drops the manifest entry. Idempotent: missing files
        or manifest entries are silently tolerated, since the goal state is
        "gone."
        """
        self._check_alive()
        if name is not None:
            _validate_name(name)

        db = _db_path(self._path, name)
        # Unlink the SQLite file plus its WAL/SHM siblings, in case the file
        # was open elsewhere and the journal hasn't been folded back in.
        for sibling in (
            db,
            db.parent / (db.name + "-wal"),
            db.parent / (db.name + "-shm"),
            db.parent / (db.name + "-journal"),
        ):
            sibling.unlink(missing_ok=True)

        if name is not None:
            subdir = self._path / name
            # Best-effort: remove the now-empty subdir. Leave it alone if the
            # caller has put other files in there.
            if subdir.exists() and not any(subdir.iterdir()):
                subdir.rmdir()
            _unregister(self._path, name)

    def destroy(self) -> None:
        """Delete the entire mount directory and invalidate this handle.

        Idempotent on missing paths. After this call, every other method on
        the handle raises `MountDestroyed`.
        """
        self._check_alive()
        if self._path.exists():
            shutil.rmtree(self._path)
        self._destroyed = True


def _register(
    mount: Path, name: str, *, model: str, description: str | None = None
) -> None:
    """Add a named DB to the manifest. Atomic; safe to call repeatedly."""
    manifest = _read_manifest(mount)
    entry: dict[str, str] = {
        "model": model,
        "created_at": datetime.now(UTC).isoformat(),
    }
    if description is not None:
        entry["description"] = description
    manifest["databases"][name] = entry
    _write_manifest(mount, manifest)


def _unregister(mount: Path, name: str) -> None:
    """Drop a named DB from the manifest. Atomic; idempotent if missing."""
    manifest = _read_manifest(mount)
    if name in manifest["databases"]:
        del manifest["databases"][name]
        _write_manifest(mount, manifest)
