"""Resolve mount, database, and embedder for a CLI invocation.

A mount is a directory that owns one or more SQLite grimoires plus a
shared embedder model cache. The anonymous default DB lives at
`<mount>/grimoire.db`; named DBs live at `<mount>/<name>/grimoire.db`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from grimoire.embed import Embedder, NoOpEmbedder
from grimoire.errors import GrimoireError, GrimoireNotFound
from grimoire.grimoire import Grimoire, peek
from grimoire.grimoire import open as open_grimoire

DEFAULT_MOUNT = Path("~/.grimoire")
DEFAULT_DB_FILE = "grimoire.db"
MODELS_DIR = "models"
MANIFEST_FILE = "grimoire.toml"
NOOP_MODEL = "noop"
DEFAULT_FASTEMBED_MODEL = "BAAI/bge-small-en-v1.5"

_RESERVED_NAMES = frozenset({DEFAULT_DB_FILE, MODELS_DIR, MANIFEST_FILE})


def validate_db_name(name: str) -> None:
    """Reject names that would clash with mount-level filenames or paths."""
    if not name:
        raise GrimoireError("DB name cannot be empty.")
    if "/" in name or "\\" in name:
        raise GrimoireError(f"DB name {name!r} cannot contain path separators.")
    if name.startswith("."):
        raise GrimoireError(f"DB name {name!r} cannot start with a dot.")
    if name in _RESERVED_NAMES:
        raise GrimoireError(f"DB name {name!r} is reserved.")
    if len(name) > 64:
        raise GrimoireError(f"DB name {name!r} exceeds 64 characters.")


class Kind(StrEnum):
    """How a new database's embedder should be constructed."""

    noop = "noop"
    fastembed = "fastembed"


class SearchMode(StrEnum):
    """Which index `grimoire search` should hit."""

    vector = "vector"
    keyword = "keyword"


@dataclass(frozen=True, slots=True)
class Mount:
    path: Path

    @property
    def default_db(self) -> Path:
        return self.path / DEFAULT_DB_FILE

    @property
    def models_dir(self) -> Path:
        return self.path / MODELS_DIR

    def db_path(self, name: str | None) -> Path:
        return self.default_db if name is None else self.path / name / DEFAULT_DB_FILE

    def exists(self) -> bool:
        return self.path.is_dir()


def resolve_mount(arg: Path | None) -> Mount:
    """Resolve `--mount` / `GRIMOIRE_MOUNT` / default to an absolute Mount."""
    raw = arg if arg is not None else DEFAULT_MOUNT
    return Mount(
        path=raw.expanduser().resolve() if not raw.is_absolute() else raw.expanduser()
    )


def require_mount(mount: Mount) -> None:
    if not mount.exists():
        raise GrimoireError(f"No mount at {mount.path}. Run `grimoire mount` first.")


def require_db(mount: Mount, name: str | None) -> Path:
    """Resolve `<mount>` + `<name?>` to an existing SQLite file path."""
    require_mount(mount)
    path = mount.db_path(name)
    if not path.exists():
        label = "default DB" if name is None else f"DB {name!r}"
        hint = (
            "`grimoire mount` creates it."
            if name is None
            else f"`grimoire create {name}` creates it."
        )
        raise GrimoireNotFound(f"No {label} at {path}. {hint}")
    return path


def make_embedder_for_create(kind: Kind, model: str | None, mount: Mount) -> Embedder:
    """Construct a fresh embedder for `grimoire mount` or `grimoire create`."""
    if kind is Kind.noop:
        return NoOpEmbedder()
    return _fastembed(model or DEFAULT_FASTEMBED_MODEL, mount)


def make_embedder_for_open(model: str, mount: Mount) -> Embedder:
    """Construct the embedder matching a DB's stored lock."""
    if model == NOOP_MODEL:
        return NoOpEmbedder()
    return _fastembed(model, mount)


def _fastembed(model: str, mount: Mount) -> Embedder:
    from grimoire.embed import FastembedEmbedder

    mount.models_dir.mkdir(parents=True, exist_ok=True)
    return FastembedEmbedder(model=model, cache_folder=mount.models_dir)


def open_db(mount: Mount, name: str | None) -> Grimoire:
    """Peek the DB to discover its embedder lock, construct it, then open."""
    path = require_db(mount, name)
    info = peek(path)
    embedder = make_embedder_for_open(info.model, mount)
    return open_grimoire(path, embedder=embedder)
