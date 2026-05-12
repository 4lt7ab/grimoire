import pytest
from grimoire.embed import NoOpEmbedder


@pytest.fixture
def fake_embedder() -> NoOpEmbedder:
    return NoOpEmbedder()


@pytest.fixture
def patched_embedder(monkeypatch, fake_embedder):
    """Replace embed.build_embedder so CLI tests don't construct fastembed.

    fastembed pulls model metadata on construction and weights on first use;
    neither is appropriate for unit tests.
    """
    monkeypatch.setattr(
        "grimoire_cli.embed.build_embedder", lambda _mount: fake_embedder
    )
    return fake_embedder
