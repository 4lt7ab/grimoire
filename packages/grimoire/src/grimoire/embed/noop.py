class NoOpEmbedder:
    """Embedder that produces zero vectors.

    For grimoires used only for keyword search, payload storage, or
    structured browsing — anywhere vector similarity has no meaning.
    `semantic_search` against a NoOp grimoire returns entries in
    arbitrary order with distance near zero; the contract is satisfied
    structurally, but the result has no ranking value.
    """

    model = "noop"
    dimension = 1

    def embed(self, text: str) -> list[float]:
        return [0.0]

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] for _ in texts]
