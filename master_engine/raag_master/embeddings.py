"""Turning code chunks into vectors.

The embedder is behind a protocol rather than called directly. Two reasons, and
the second is the one that matters day to day:

* Embedding models are the most replaceable part of a retrieval system. A
  better code-specific model appearing next month should be a one-line change,
  not a refactor.
* Tests must not download a model or reach the network. A deterministic
  in-process implementation makes the whole indexing pipeline testable in
  milliseconds, which is the difference between a test suite that runs on every
  save and one that runs when someone remembers.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence
from typing import Protocol, runtime_checkable

__all__ = [
    "Embedder",
    "FastEmbedEmbedder",
    "HashEmbedder",
    "default_embedder",
]


@runtime_checkable
class Embedder(Protocol):
    """Anything that turns text into fixed-width vectors."""

    @property
    def dimensions(self) -> int:
        """Vector width. Must match the collection the vectors are stored in."""
        ...

    @property
    def name(self) -> str:
        """Identifier recorded alongside the index.

        A collection built with one model and queried with another returns
        confident nonsense — the vectors occupy the same space but mean
        different things. Storing the name makes that mismatch detectable.
        """
        ...

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed a batch of texts, preserving order."""
        ...


class HashEmbedder:
    """Deterministic embeddings from hashed token features.

    NOT SEMANTIC. Two chunks describing the same idea in different words embed
    to unrelated points. This exists so the indexing pipeline can be tested and
    developed without a model download or network access, and so CI stays fast.

    It is a real embedding in the mechanical sense — stable, normalised, fixed
    width, and similar for texts sharing vocabulary — which is enough to verify
    that chunking, upserting, filtering, and ranking are wired correctly. It is
    not enough to evaluate retrieval quality, and using it in production would
    quietly reduce the retriever to keyword overlap.
    """

    def __init__(self, dimensions: int = 384) -> None:
        if dimensions <= 0:
            raise ValueError("dimensions must be positive")
        self._dimensions = dimensions

    @property
    def dimensions(self) -> int:
        return self._dimensions

    @property
    def name(self) -> str:
        return f"hash-{self._dimensions}"

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> list[float]:
        vector = [0.0] * self._dimensions

        for token in self._tokenise(text):
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            bucket = int.from_bytes(digest[:4], "little") % self._dimensions
            # Sign from a separate byte so a token can cancel rather than only
            # accumulate; otherwise every vector drifts toward one orthant and
            # cosine similarity flattens.
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[bucket] += sign

        return _normalise(vector)

    @staticmethod
    def _tokenise(text: str) -> list[str]:
        """Split on non-alphanumerics, lowercased.

        Crude by design: identifiers, keywords, and literals all become tokens.
        Splitting camelCase and snake_case would improve overlap, but this is a
        test double, and a better test double is not the goal.
        """
        tokens: list[str] = []
        current: list[str] = []

        for character in text:
            if character.isalnum() or character == "_":
                current.append(character.lower())
            elif current:
                tokens.append("".join(current))
                current = []

        if current:
            tokens.append("".join(current))

        return tokens


class FastEmbedEmbedder:
    """Real semantic embeddings via fastembed.

    fastembed runs ONNX models in-process — no API key, no network at query
    time, no per-token cost, and it works offline once the model is cached. For
    a tool that may be pointed at a private repository, keeping source code off
    third-party servers is a meaningful property rather than only a convenience.

    The default model is a general-purpose sentence embedder rather than a
    code-specific one. Code-specific models exist and would likely retrieve
    better; this one is chosen for size and install reliability. Swapping it is
    a constructor argument, which is the point of the protocol.
    """

    DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"

    def __init__(self, model_name: str = DEFAULT_MODEL) -> None:
        try:
            from fastembed import TextEmbedding
        except ImportError as error:  # pragma: no cover - depends on install
            raise RuntimeError(
                "fastembed is not installed. Install it with "
                "`pip install fastembed`, or use HashEmbedder for offline work."
            ) from error

        # Downloads and caches on first construction, not on first call, so the
        # delay happens somewhere a user expects it.
        self._model = TextEmbedding(model_name=model_name)
        self._model_name = model_name
        self._dimensions = len(next(iter(self._model.embed(["dimension probe"]))))

    @property
    def dimensions(self) -> int:
        return self._dimensions

    @property
    def name(self) -> str:
        return self._model_name

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        return [vector.tolist() for vector in self._model.embed(list(texts))]


def default_embedder(*, prefer_semantic: bool = True) -> Embedder:
    """The embedder to use when the caller has no preference.

    Falls back to hashing when fastembed is unavailable, and says so. Silently
    degrading to non-semantic retrieval would be worse than failing: the index
    would build, the searches would return results, and the results would be
    keyword matches presented as semantic ones.
    """
    if prefer_semantic:
        try:
            return FastEmbedEmbedder()
        except RuntimeError as error:
            print(f"warning: {error}")
            print(
                "warning: falling back to HashEmbedder — retrieval will not be semantic"
            )

    return HashEmbedder()


def _normalise(vector: list[float]) -> list[float]:
    """Scale to unit length so cosine similarity reduces to a dot product.

    A zero vector — from empty or purely symbolic input — is returned unchanged
    rather than divided by zero. It will match nothing, which is the correct
    behaviour for a chunk with no tokens.
    """
    magnitude = math.sqrt(sum(component * component for component in vector))
    if magnitude == 0.0:
        return vector
    return [component / magnitude for component in vector]
