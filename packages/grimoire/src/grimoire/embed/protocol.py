from typing import Protocol


class Embedder(Protocol):
    """The shape grimoire needs from any text embedder.

    Implementations live alongside this module under `grimoire.embed.*`.
    `model` and `dimension` are written into a grimoire file on first
    create and validated on every reopen.
    """

    @property
    def model(self) -> str: ...

    @property
    def dimension(self) -> int: ...

    def embed(self, text: str) -> list[float]: ...

    def embed_many(self, texts: list[str]) -> list[list[float]]: ...
