from pathlib import Path


class FastembedEmbedder:
    """Embedder backed by Qdrant's `fastembed` library (ONNX Runtime).

    Requires the optional extra: `pip install grimoire[fastembed]`.

    `cache_folder` is required — the library does not pick a default
    filesystem location on the caller's behalf.
    """

    def __init__(
        self,
        model_name: str = "BAAI/bge-small-en-v1.5",
        *,
        cache_folder: str | Path,
        threads: int | None = None,
    ) -> None:
        try:
            from fastembed import TextEmbedding
        except ImportError as exc:
            raise ImportError(
                "FastembedEmbedder requires the `fastembed` extra. "
                "Install with: pip install grimoire[fastembed]"
            ) from exc

        self._model_name = model_name
        self._model = TextEmbedding(
            model_name=model_name,
            cache_dir=str(cache_folder),
            threads=threads,
        )
        # Determine dimension by embedding a probe — works regardless of
        # fastembed's internal model registry shape.
        [probe] = list(self._model.embed(["dimension probe"]))
        self._dimension = len(probe)

    @property
    def model(self) -> str:
        return self._model_name

    @property
    def dimension(self) -> int:
        return self._dimension

    def embed(self, text: str) -> list[float]:
        [vector] = list(self._model.embed([text]))
        return vector.tolist()

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        return [v.tolist() for v in self._model.embed(texts)]
