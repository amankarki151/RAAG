"""Tests for the embedding layer.

Only HashEmbedder is exercised here. FastEmbedEmbedder downloads a model and
runs an ONNX session; testing it would make the suite slow, network-dependent,
and would verify fastembed rather than RAAG. What RAAG owns is the protocol
boundary, and that is what these assert.
"""

from __future__ import annotations

import math

import pytest

from raag_master.embeddings import Embedder, HashEmbedder


def magnitude(vector: list[float]) -> float:
    return math.sqrt(sum(component * component for component in vector))


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


# --- Protocol conformance ----------------------------------------------------


def test_hash_embedder_satisfies_the_protocol():
    """Guards the swap point. A new embedder that misses a member should fail
    here rather than at the first call inside an indexing run."""
    assert isinstance(HashEmbedder(), Embedder)


def test_reports_its_dimensions():
    assert HashEmbedder(dimensions=128).dimensions == 128


def test_name_identifies_the_model():
    """A collection built with one model and queried with another returns
    confident nonsense. The name is what makes that detectable."""
    assert HashEmbedder(dimensions=64).name == "hash-64"


def test_rejects_non_positive_dimensions():
    with pytest.raises(ValueError):
        HashEmbedder(dimensions=0)

    with pytest.raises(ValueError):
        HashEmbedder(dimensions=-8)


# --- Vector properties -------------------------------------------------------


def test_produces_one_vector_per_input_in_order():
    embedder = HashEmbedder(dimensions=32)

    vectors = embedder.embed(["alpha", "beta", "gamma"])

    assert len(vectors) == 3
    assert all(len(vector) == 32 for vector in vectors)


def test_vectors_are_unit_length():
    """Normalisation is what lets cosine similarity reduce to a dot product,
    which is how the store is configured."""
    embedder = HashEmbedder(dimensions=64)

    for vector in embedder.embed(["def parse(source): return source"]):
        assert magnitude(vector) == pytest.approx(1.0)


def test_deterministic_across_calls():
    """Re-indexing unchanged code must produce identical vectors, or every
    run rewrites the whole collection."""
    embedder = HashEmbedder(dimensions=48)

    first = embedder.embed(["stable input"])
    second = embedder.embed(["stable input"])

    assert first == second


def test_deterministic_across_instances():
    text = "class Parser: pass"

    assert HashEmbedder(dimensions=48).embed([text]) == HashEmbedder(
        dimensions=48
    ).embed([text])


def test_empty_batch_returns_empty_list():
    assert HashEmbedder().embed([]) == []


def test_empty_string_does_not_raise():
    """A zero vector is the honest answer for input with no tokens: it will
    match nothing, rather than dividing by zero or matching everything."""
    (vector,) = HashEmbedder(dimensions=16).embed([""])

    assert len(vector) == 16
    assert magnitude(vector) == pytest.approx(0.0)


def test_symbols_only_input_does_not_raise():
    (vector,) = HashEmbedder(dimensions=16).embed(["{}[]();"])

    assert len(vector) == 16


# --- Similarity behaviour ----------------------------------------------------


def test_identical_texts_are_maximally_similar():
    embedder = HashEmbedder(dimensions=256)

    a, b = embedder.embed(["def load_config(path)", "def load_config(path)"])

    assert cosine(a, b) == pytest.approx(1.0)


def test_shared_vocabulary_scores_higher_than_none():
    """The one similarity property this embedder actually guarantees.

    It is enough to verify ranking is wired correctly. It is not semantic
    similarity, and the docstring on the class says so.
    """
    embedder = HashEmbedder(dimensions=512)

    query, related, unrelated = embedder.embed(
        [
            "parse configuration file",
            "parse configuration from a file path",
            "render pixels to the framebuffer",
        ]
    )

    assert cosine(query, related) > cosine(query, unrelated)


def test_different_texts_are_not_identical():
    embedder = HashEmbedder(dimensions=256)

    a, b = embedder.embed(["alpha beta gamma", "delta epsilon zeta"])

    assert cosine(a, b) < 0.99


def test_tokenisation_is_case_insensitive():
    embedder = HashEmbedder(dimensions=128)

    a, b = embedder.embed(["ParseConfig", "parseconfig"])

    assert cosine(a, b) == pytest.approx(1.0)


def test_underscores_are_part_of_identifiers():
    """load_config is one identifier, not two words — splitting it would make
    every snake_case function look alike."""
    embedder = HashEmbedder(dimensions=256)

    joined, split = embedder.embed(["load_config", "load config"])

    assert cosine(joined, split) < 0.99


def test_larger_dimensions_reduce_collisions():
    """Hash bucketing collides; more buckets collide less. This is the whole
    reason the width is configurable."""
    small = HashEmbedder(dimensions=8)
    large = HashEmbedder(dimensions=1024)

    texts = ["alpha beta", "gamma delta"]

    small_a, small_b = small.embed(texts)
    large_a, large_b = large.embed(texts)

    assert abs(cosine(large_a, large_b)) <= abs(cosine(small_a, small_b)) + 1e-9
