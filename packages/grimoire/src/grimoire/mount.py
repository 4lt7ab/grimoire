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
import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import tomli_w

from grimoire.errors import InvalidMount, MountDestroyed

if TYPE_CHECKING:
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


def _resolve_mount(path: str | Path | None) -> Path:
    """Resolve the mount path. Order: explicit arg > GRIMOIRE_MOUNT env > default."""
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


class Mount:
    """A handle to a grimoire mount directory.

    Construct via `Grimoire.mount(path)` rather than directly. Operations on
    a destroyed handle raise `MountDestroyed`.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._destroyed = False

    @property
    def path(self) -> Path:
        self._check_alive()
        return self._path

    def _check_alive(self) -> None:
        if self._destroyed:
            raise MountDestroyed(
                f"Mount at {self._path} has been destroyed; handle is unusable"
            )

    def has(self, name: str | None) -> bool:
        """True if a database with this name exists in the mount."""
        self._check_alive()
        if name is not None:
            _validate_name(name)
        return _db_path(self._path, name).exists()

    def path_for(self, name: str | None) -> Path:
        """Resolve the SQLite file path for `name` in this mount.

        `None` resolves to the default at `<mount>/grimoire.db`; a name to
        `<mount>/<name>/grimoire.db`. Useful when constructing a path to hand
        to lower-level operations.
        """
        self._check_alive()
        if name is not None:
            _validate_name(name)
        return _db_path(self._path, name)

    def peek(self, name: str | None) -> Stats | None:
        """Return Stats for a database without opening it. None if missing."""
        # Imported lazily to avoid the circular core <-> mount import.
        from grimoire.core import Grimoire

        self._check_alive()
        if name is not None:
            _validate_name(name)
        return Grimoire.peek(_db_path(self._path, name))

    def list(self) -> list[DbInfo]:
        """Return DbInfo for every database in the mount.

        Walks the default DB (if present at `<mount>/grimoire.db`) first, then
        each named DB in the manifest in alphabetical order. Manifest entries
        whose files are missing are silently skipped — the goal is reflecting
        actual on-disk state, not the manifest's wishes.
        """
        from grimoire.core import Grimoire

        self._check_alive()
        infos: list[DbInfo] = []

        default_path = _db_path(self._path, None)
        default_stats = Grimoire.peek(default_path)
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
            stats = Grimoire.peek(db_path)
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
