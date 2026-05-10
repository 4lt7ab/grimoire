from pathlib import Path


class FastembedEmbedder:
    """Local ONNX embedder backed by the `fastembed` package.

    Requires the `fastembed` extra. Dimension is resolved from the model
    catalog at construction so it is known before any weights load.
    """

    def __init__(
        self,
        model: str = "BAAI/bge-small-en-v1.5",
        *,
        cache_folder: str | Path | None = None,
    ) -> None:
        try:
            from fastembed import TextEmbedding
        except ImportError as e:
            raise ImportError(
                "FastembedEmbedder requires the 'fastembed' extra. "
                "Install with: pip install '4lt7ab-grimoire[fastembed]'"
            ) from e

        match = next(
            (d for d in TextEmbedding.list_supported_models() if d["model"] == model),
            None,
        )
        if match is None:
            raise ValueError(f"Unknown fastembed model: {model!r}")

        self._model = model
        self._dimension = int(match["dim"])
        self._impl = TextEmbedding(
            model_name=model,
            cache_dir=str(cache_folder) if cache_folder is not None else None,
        )

    @property
    def model(self) -> str:
        return self._model

    @property
    def dimension(self) -> int:
        return self._dimension

    def embed(self, text: str) -> list[float]:
        [vec] = self._impl.embed([text])
        return vec.tolist()

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        return [v.tolist() for v in self._impl.embed(texts)]
