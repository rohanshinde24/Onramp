"""Text embeddings for dense policy retrieval. The model runs locally on CPU."""

from collections.abc import Sequence
from typing import Any, Protocol

DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"


class Embedder(Protocol):
    @property
    def dimensions(self) -> int: ...

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class FastEmbedder:
    """ONNX embedding model via fastembed. Requires the ``embeddings`` extra."""

    def __init__(self, model_name: str = DEFAULT_MODEL) -> None:
        from fastembed import TextEmbedding  # imported lazily: optional dependency

        self._model: Any = TextEmbedding(model_name=model_name)
        self._dimensions = len(self.embed_query("dimension probe"))

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [[float(x) for x in vector] for vector in self._model.passage_embed(list(texts))]

    def embed_query(self, text: str) -> list[float]:
        (vector,) = list(self._model.query_embed([text]))
        return [float(x) for x in vector]
