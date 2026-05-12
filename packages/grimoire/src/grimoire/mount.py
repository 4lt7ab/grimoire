import re
import shutil
from dataclasses import dataclass
from pathlib import Path

DB_FILENAME = "grimoire.db"
REGISTRY_FILENAME = "grimoire.toml"
MODELS_DIRNAME = "models"
DEFAULT_MOUNT = Path.home() / ".grimoire"

_NAME_PATTERN = re.compile(r"^[a-z0-9_-]+$")


def _normalize_name(name: str) -> str:
    lowered = name.lower()
    if not _NAME_PATTERN.fullmatch(lowered):
        raise ValueError(
            f"Invalid database name {name!r}: names must be non-empty and "
            "contain only alphanumerics, hyphens, and underscores."
        )
    return lowered


@dataclass(frozen=True, slots=True)
class Mount:
    """A directory holding one or more grimoire SQLite files plus a shared model cache.

    Layout:
        <path>/grimoire.db          -- the default DB
        <path>/<name>/grimoire.db   -- a named DB
        <path>/models/              -- shared embedder model cache
        <path>/grimoire.toml        -- registry file (reserved; currently inert)

    The library publishes the convention; consumers (CLI, services, scripts)
    decide where the mount lives on disk and which embedder to pair with it.
    """

    path: Path = DEFAULT_MOUNT

    @property
    def registry_path(self) -> Path:
        return self.path / REGISTRY_FILENAME

    @property
    def models_dir(self) -> Path:
        return self.path / MODELS_DIRNAME

    @property
    def default_db(self) -> Path:
        return self.path / DB_FILENAME

    def db_path(self, name: str | None) -> Path:
        if name is None:
            return self.default_db
        return self.path / _normalize_name(name) / DB_FILENAME

    def exists(self) -> bool:
        return (
            self.registry_path.exists()
            and self.models_dir.exists()
            and self.default_db.exists()
        )


def create(mount: Mount) -> None:
    """Create the mount layout on disk. Idempotent.

    Touches `default_db` so the file exists; the caller still has to
    `grimoire.open(mount.default_db, embedder=...)` to install the schema
    and write the embedder lock.
    """
    Path.mkdir(mount.path, parents=True, exist_ok=True)
    Path.mkdir(mount.models_dir, exist_ok=True)
    Path.touch(mount.registry_path, exist_ok=True)
    Path.touch(mount.default_db, exist_ok=True)


def destroy(mount: Mount) -> None:
    """Remove the entire mount directory. No undo."""
    shutil.rmtree(mount.path)
