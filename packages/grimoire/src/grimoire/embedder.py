from typing import Protocol, runtime_checkable


@runtime_checkable
class Embedder(Protocol):
    """Produces fixed-dimension vectors for text. Caller-supplied to a Grimoire."""

    @property
    def model(self) -> str: ...

    @property
    def dimension(self) -> int: ...

    def embed(self, text: str) -> list[float]: ...
