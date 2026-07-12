import hashlib
import math
from typing import List, Protocol


class EmbeddingProvider(Protocol):
    dimension: int

    def embed(self, text: str) -> List[float]: ...


class HashingEmbeddingProvider:
    """Deterministic fallback embedding provider.

    This is not a replacement for a domain embedding model. It gives the
    retrieval pipeline stable vector behavior in local/test environments where
    sentence-transformers is unavailable.
    """

    def __init__(self, dimension: int = 768) -> None:
        self.dimension = dimension

    def embed(self, text: str) -> List[float]:
        vector = [0.0] * self.dimension
        for token in tokenize_for_embedding(text):
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimension
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[index] += sign
        return normalize(vector)


class SentenceTransformersEmbeddingProvider:
    def __init__(self, model_name: str, dimension: int = 768) -> None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise RuntimeError("sentence-transformers is required for this embedding backend") from exc
        self.model = SentenceTransformer(model_name)
        self.dimension = dimension
        self.model_name = model_name
        probe = self.embed("embedding dimension validation")
        if len(probe) != self.dimension:
            raise ValueError(
                f"Embedding model {model_name} returned {len(probe)} dimensions; "
                f"expected {self.dimension}. Use a {self.dimension}-dimensional model or migrate the vector schema."
            )

    def embed(self, text: str) -> List[float]:
        embedding = self.model.encode(text, normalize_embeddings=True)
        values = [float(value) for value in embedding]
        if len(values) != self.dimension:
            raise ValueError(
                f"Embedding model {self.model_name} returned {len(values)} dimensions; expected {self.dimension}."
            )
        return values


def create_embedding_provider(backend: str, model_name: str, dimension: int) -> EmbeddingProvider:
    if backend == "hashing":
        return HashingEmbeddingProvider(dimension=dimension)
    if backend == "sentence-transformers":
        return SentenceTransformersEmbeddingProvider(model_name=model_name, dimension=dimension)
    raise ValueError(f"Unsupported embedding backend: {backend}")


def cosine_similarity(left: List[float], right: List[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    return sum(a * b for a, b in zip(left, right))


def normalize(vector: List[float]) -> List[float]:
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0:
        return vector
    return [value / norm for value in vector]


def tokenize_for_embedding(text: str) -> List[str]:
    return [token.lower() for token in text.replace("-", " ").split() if token.strip()]
