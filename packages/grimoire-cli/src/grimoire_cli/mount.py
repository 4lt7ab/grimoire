import os
import shutil
from dataclasses import dataclass
from pathlib import Path

DEFAULT_MOUNT = Path.home() / ".grimoire"
ENV_VAR = "GRIMOIRE_MOUNT"
DB_FILENAME = "grimoire.db"
REGISTRY_FILENAME = "grimoire.toml"
MODELS_DIRNAME = "models"


@dataclass(frozen=True, slots=True)
class Mount:
    path: Path

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
        return self.path / name / DB_FILENAME

    def exists(self) -> bool:
        return (
            self.registry_path.exists()
            and self.models_dir.exists()
            and self.default_db.exists()
        )


def resolve(path: Path | None = None) -> Mount:
    if path is None:
        env = os.environ.get(ENV_VAR)
        path = Path(env) if env else DEFAULT_MOUNT
    return Mount(path.expanduser().resolve())


def create(mount: Mount) -> None:
    Path.mkdir(mount.path, parents=True, exist_ok=True)
    Path.mkdir(mount.models_dir, exist_ok=True)
    Path.touch(mount.registry_path, exist_ok=True)
    Path.touch(mount.default_db, exist_ok=True)


def destroy(mount: Mount) -> None:
    shutil.rmtree(mount.path)
