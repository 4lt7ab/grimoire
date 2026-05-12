import os
from pathlib import Path

from grimoire.mount import (
    DB_FILENAME,
    DEFAULT_MOUNT,
    MODELS_DIRNAME,
    REGISTRY_FILENAME,
    Mount,
    create,
    destroy,
)

__all__ = [
    "DB_FILENAME",
    "DEFAULT_MOUNT",
    "ENV_VAR",
    "MODELS_DIRNAME",
    "Mount",
    "REGISTRY_FILENAME",
    "create",
    "destroy",
    "resolve",
]

ENV_VAR = "GRIMOIRE_MOUNT"


def resolve(path: Path | None = None) -> Mount:
    """Resolve a `Mount` from explicit path, `GRIMOIRE_MOUNT` env, or default."""
    if path is not None:
        return Mount(path.expanduser().resolve())
    env = os.environ.get(ENV_VAR)
    if env is not None:
        return Mount(Path(env).expanduser().resolve())
    return Mount()
