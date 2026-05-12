from pathlib import Path

from grimoire.embed import Embedder, FastembedEmbedder

DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"


def build_embedder(cache_dir: Path) -> Embedder:
    """Construct the default embedder backed by the given model cache directory."""
    return FastembedEmbedder(DEFAULT_MODEL, cache_folder=cache_dir)
