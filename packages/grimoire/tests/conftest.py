import pytest


class _FakeEmbedder:
    """Test stand-in. Reports configurable model/dimension so SQL-plumbing
    tests can exercise the schema at realistic dimensions without paying
    real embedder cost. `embed`/`embed_many` return zero vectors —
    plumbing tests insert their own vectors via raw SQL.
    """

    def __init__(self, *, model: str = "fake", dimension: int = 384) -> None:
        self._model = model
        self._dimension = dimension

    @property
    def model(self) -> str:
        return self._model

    @property
    def dimension(self) -> int:
        return self._dimension

    def embed(self, text: str) -> list[float]:
        return [0.0] * self._dimension

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] * self._dimension for _ in texts]


@pytest.fixture
def fake_embedder() -> _FakeEmbedder:
    return _FakeEmbedder()
