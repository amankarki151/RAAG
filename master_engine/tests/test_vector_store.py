"""Tests for vector storage and structurally-filtered retrieval.

Run against Qdrant's in-memory client rather than a mock. A mock would agree
with whatever the code does; the real client disagrees when the code is wrong,
which is the entire value of the test.
"""

from __future__ import annotations

import pytest

from raag_master.chunking import CodeChunk
from raag_master.embeddings import HashEmbedder
from raag_master.vector_store import ChunkPayload, VectorStore

DIMENSIONS = 64


def chunk(
    chunk_id: str,
    path: str,
    name: str,
    text: str,
    *,
    kind: str = "function",
    parent: str | None = None,
) -> CodeChunk:
    return CodeChunk(
        chunk_id=chunk_id,
        path=path,
        kind=kind,
        name=name,
        qualified_name=f"{parent}.{name}" if parent else name,
        parent_class=parent,
        text=text,
        byte_start=0,
        byte_end=len(text),
        line_start=1,
        line_end=text.count("\n") + 1,
    )


@pytest.fixture
def embedder() -> HashEmbedder:
    return HashEmbedder(dimensions=DIMENSIONS)


@pytest.fixture
def store() -> VectorStore:
    store = VectorStore.in_memory("test_chunks", DIMENSIONS)
    store.ensure_collection()
    return store


@pytest.fixture
def populated(store: VectorStore, embedder: HashEmbedder) -> VectorStore:
    chunks = [
        chunk(
            "c1", "parser.py", "parse_config", "def parse_config(path): read the file"
        ),
        chunk(
            "c2", "parser.py", "parse_header", "def parse_header(data): read the header"
        ),
        chunk("c3", "render.py", "draw_frame", "def draw_frame(buffer): draw pixels"),
        chunk(
            "c4", "network.py", "send_packet", "def send_packet(data): transmit bytes"
        ),
    ]
    vectors = embedder.embed([c.embedding_text() for c in chunks])

    store.upsert(
        chunks,
        vectors,
        metrics_by_path={
            "parser.py": (12, 3, 0.20),
            "render.py": (1, 9, 0.90),
        },
    )
    return store


# --- Collection lifecycle ----------------------------------------------------


def test_ensure_collection_is_idempotent(store: VectorStore):
    store.ensure_collection()
    store.ensure_collection()

    assert store.count() == 0


def test_recreate_clears_existing_points(populated: VectorStore):
    assert populated.count() == 4

    populated.ensure_collection(recreate=True)

    assert populated.count() == 0


# --- Upserting ---------------------------------------------------------------


def test_upsert_stores_every_chunk(populated: VectorStore):
    assert populated.count() == 4


def test_upserting_the_same_chunk_twice_overwrites(
    store: VectorStore, embedder: HashEmbedder
):
    """Deterministic point IDs are what make re-indexing safe. Without them a
    second run doubles the collection."""
    one = [chunk("c1", "a.py", "f", "def f(): pass")]
    vectors = embedder.embed([c.embedding_text() for c in one])

    store.upsert(one, vectors)
    store.upsert(one, vectors)

    assert store.count() == 1


def test_vector_count_mismatch_is_rejected(store: VectorStore, embedder: HashEmbedder):
    chunks = [chunk("c1", "a.py", "f", "body"), chunk("c2", "a.py", "g", "body")]
    vectors = embedder.embed(["only one"])

    with pytest.raises(ValueError, match="mismatch"):
        store.upsert(chunks, vectors)


def test_wrong_dimension_vector_is_rejected(store: VectorStore):
    """Fails at upsert with a clear message rather than at query time with a
    dimension error from deep inside the client."""
    chunks = [chunk("c1", "a.py", "f", "body")]

    with pytest.raises(ValueError, match="dimensions"):
        store.upsert(chunks, [[0.1] * (DIMENSIONS + 1)])


def test_upserting_nothing_is_harmless(store: VectorStore):
    assert store.upsert([], []) == 0
    assert store.count() == 0


def test_batching_stores_everything(store: VectorStore, embedder: HashEmbedder):
    chunks = [chunk(f"c{i}", "a.py", f"f{i}", f"def f{i}(): pass") for i in range(50)]
    vectors = embedder.embed([c.embedding_text() for c in chunks])

    store.upsert(chunks, vectors, batch_size=7)

    assert store.count() == 50


# --- Payloads ----------------------------------------------------------------


def test_metrics_are_attached_to_payloads(
    populated: VectorStore, embedder: HashEmbedder
):
    """The reason payloads exist. A retrieved chunk arrives carrying its
    structural context, not just its text."""
    (query,) = embedder.embed(["parse the configuration file"])

    results = populated.search(query, limit=10, paths=["parser.py"])

    assert results
    assert all(result.payload.afferent_coupling == 12 for result in results)
    assert all(result.payload.instability == pytest.approx(0.20) for result in results)


def test_files_without_metrics_store_zeros(
    populated: VectorStore, embedder: HashEmbedder
):
    """A file with no computed metrics is a real case; refusing to index it
    would leave gaps in retrieval for no benefit."""
    (query,) = embedder.embed(["transmit bytes over the network"])

    results = populated.search(query, limit=10, paths=["network.py"])

    assert results
    assert results[0].payload.afferent_coupling == 0


def test_payload_round_trips_every_field(
    populated: VectorStore, embedder: HashEmbedder
):
    (query,) = embedder.embed(["parse the header"])

    (top, *_) = populated.search(query, limit=1, paths=["parser.py"])
    payload = top.payload

    assert payload.path == "parser.py"
    assert payload.kind == "function"
    assert payload.name in {"parse_config", "parse_header"}
    assert payload.line_start >= 1
    assert isinstance(payload.text, str)


def test_parent_class_survives_none():
    payload = ChunkPayload.from_dict({"path": "a.py", "parent_class": None})

    assert payload.parent_class is None


def test_citation_is_human_followable(populated: VectorStore, embedder: HashEmbedder):
    (query,) = embedder.embed(["parse config"])

    (top, *_) = populated.search(query, limit=1)

    citation = top.citation()
    assert ":" in citation
    assert "-" in citation


# --- Search ------------------------------------------------------------------


def test_search_returns_results_ranked_by_score(
    populated: VectorStore, embedder: HashEmbedder
):
    (query,) = embedder.embed(["parse the configuration file"])

    results = populated.search(query, limit=4)

    scores = [result.score for result in results]
    assert scores == sorted(scores, reverse=True)


def test_limit_is_respected(populated: VectorStore, embedder: HashEmbedder):
    (query,) = embedder.embed(["anything"])

    assert len(populated.search(query, limit=2)) <= 2


def test_search_on_empty_collection_returns_nothing(
    store: VectorStore, embedder: HashEmbedder
):
    (query,) = embedder.embed(["anything at all"])

    assert store.search(query) == []


# --- Structural filtering: the GraphRAG boundary -----------------------------


def test_path_filter_restricts_results(populated: VectorStore, embedder: HashEmbedder):
    """The parameter that makes this GraphRAG rather than RAG.

    Similarity ranks only within the files a change can actually reach.
    """
    (query,) = embedder.embed(["draw pixels to the buffer"])

    results = populated.search(query, limit=10, paths=["parser.py"])

    assert results
    assert all(result.payload.path == "parser.py" for result in results)


def test_unfiltered_search_can_return_any_file(
    populated: VectorStore, embedder: HashEmbedder
):
    (query,) = embedder.embed(["draw pixels to the buffer"])

    results = populated.search(query, limit=10)

    assert {result.payload.path for result in results} > {"parser.py"}


def test_filter_accepts_several_paths(populated: VectorStore, embedder: HashEmbedder):
    (query,) = embedder.embed(["anything"])

    results = populated.search(query, limit=10, paths=["parser.py", "render.py"])

    assert {result.payload.path for result in results} <= {"parser.py", "render.py"}


def test_empty_path_list_returns_nothing(
    populated: VectorStore, embedder: HashEmbedder
):
    """An empty blast radius means nothing is reachable.

    Falling through to an unfiltered search here would silently return the
    whole repository — the exact opposite of what was asked for, and a bug
    that would look like the system working.
    """
    (query,) = embedder.embed(["parse the configuration"])

    assert populated.search(query, limit=10, paths=[]) == []


def test_none_paths_differs_from_empty_paths(
    populated: VectorStore, embedder: HashEmbedder
):
    (query,) = embedder.embed(["parse the configuration"])

    assert populated.search(query, limit=10, paths=None) != []
    assert populated.search(query, limit=10, paths=[]) == []


def test_filter_on_unknown_path_returns_nothing(
    populated: VectorStore, embedder: HashEmbedder
):
    (query,) = embedder.embed(["anything"])

    assert populated.search(query, limit=10, paths=["does/not/exist.py"]) == []


def test_min_score_drops_weak_matches(populated: VectorStore, embedder: HashEmbedder):
    (query,) = embedder.embed(["parse the configuration file"])

    strict = populated.search(query, limit=10, min_score=0.99)
    loose = populated.search(query, limit=10)

    assert len(strict) <= len(loose)


# --- Deletion ----------------------------------------------------------------


def test_delete_paths_removes_only_those_files(populated: VectorStore):
    """Needed for incremental re-indexing: a deleted file leaves vectors that
    would still be retrieved as current code."""
    populated.delete_paths(["parser.py"])

    assert populated.count() == 2


def test_deleting_nothing_is_harmless(populated: VectorStore):
    populated.delete_paths([])

    assert populated.count() == 4


def test_deleting_an_unknown_path_is_harmless(populated: VectorStore):
    populated.delete_paths(["never/indexed.py"])

    assert populated.count() == 4
