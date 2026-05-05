from typing import Protocol, runtime_checkable


@runtime_checkable
class Embedder(Protocol):
    """Produces fixed-dimension vectors for text. Caller-supplied to a Grimoire.

    `embed` handles single-record paths (`Grimoire.add`, search). `embed_many`
    handles bulk paths (`Grimoire.add_many`) and is expected to be more
    efficient than `len(texts)` separate `embed` calls — most embedding
    libraries amortize tokenization, model dispatch, or device transfers
    across a batch.
    """

    @property
    def model(self) -> str: ...

    @property
    def dimension(self) -> int: ...

    def embed(self, text: str) -> list[float]: ...

    def embed_many(self, texts: list[str]) -> list[list[float]]: ...
