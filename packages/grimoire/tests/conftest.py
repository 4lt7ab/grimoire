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
        self.embed_calls = 0
        self.embed_many_calls = 0

    @property
    def model(self) -> str:
        return self._model

    @property
    def dimension(self) -> int:
        return self._dimension

    def embed(self, text: str) -> list[float]:
        self.embed_calls += 1
        return [0.0] * self._dimension

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        self.embed_many_calls += 1
        return [[0.0] * self._dimension for _ in texts]


@pytest.fixture
def fake_embedder() -> _FakeEmbedder:
    return _FakeEmbedder()
