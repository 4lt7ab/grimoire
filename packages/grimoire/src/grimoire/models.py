from dataclasses import dataclass, field
from typing import Any


@dataclass
class Entry:
    id: str
    kind: str
    content: str
    payload: dict[str, Any] | None = None
    threshold: float | None = None
    distance: float | None = None


@dataclass
class Stats:
    model: str
    dimension: int
    schema_version: int
    entry_count: int
    kinds: dict[str, int] = field(default_factory=dict)
