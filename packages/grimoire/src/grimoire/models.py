from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ulid import ULID


@dataclass
class Entry:
    id: str
    content: str
    group_key: str | None = None
    group_ref: str | None = None
    payload: dict[str, Any] | None = None
    threshold: float | None = None
    keywords: list[str] | None = None
    distance: float | None = None
    rank: float | None = None

    @property
    def created_at(self) -> datetime:
        return ULID.from_str(self.id).datetime


@dataclass
class Stats:
    model: str
    dimension: int
    schema_version: int
    entry_count: int
    groups: dict[str, int] = field(default_factory=dict)
