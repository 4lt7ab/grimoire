"""Read and write the mount's `grimoire.toml` registry.

The manifest tracks named databases only — the anonymous default DB at
`<mount>/grimoire.db` is implicit and never appears in the toml.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

import tomli_w

MANIFEST_NAME = "grimoire.toml"
SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class DbRecord:
    name: str
    model: str
    created_at: str
    description: str | None = None


def manifest_path(mount: Path) -> Path:
    return mount / MANIFEST_NAME


def read(mount: Path) -> dict[str, DbRecord]:
    path = manifest_path(mount)
    if not path.exists():
        return {}
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    raw = data.get("databases", {})
    return {
        name: DbRecord(
            name=name,
            model=entry["model"],
            created_at=entry["created_at"],
            description=entry.get("description"),
        )
        for name, entry in raw.items()
    }


def write(mount: Path, records: dict[str, DbRecord]) -> None:
    doc: dict[str, object] = {"schema_version": SCHEMA_VERSION}
    if records:
        doc["databases"] = {
            name: _record_to_toml(rec) for name, rec in sorted(records.items())
        }
    path = manifest_path(mount)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(tomli_w.dumps(doc).encode("utf-8"))
    tmp.replace(path)


def _record_to_toml(rec: DbRecord) -> dict[str, str]:
    out: dict[str, str] = {"model": rec.model, "created_at": rec.created_at}
    if rec.description is not None:
        out["description"] = rec.description
    return out


def add(mount: Path, record: DbRecord) -> None:
    records = read(mount)
    records[record.name] = record
    write(mount, records)


def remove(mount: Path, name: str) -> None:
    records = read(mount)
    records.pop(name, None)
    write(mount, records)


def init(mount: Path) -> None:
    """Create an empty manifest if one does not yet exist."""
    if not manifest_path(mount).exists():
        write(mount, {})
