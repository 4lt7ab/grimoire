from dataclasses import dataclass


@dataclass
class Entry:
    id: str
    kind: str
    content: str
    payload: str | None = None
    threshold: float | None = None
    distance: float | None = None
