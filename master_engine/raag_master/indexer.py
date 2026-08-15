"""The indexing pipeline: snapshot in, searchable vectors out.

Ties together the pieces that are each independently testable — chunking,
embedding, storage — and adds the one thing none of them can do alone: attach
each file's coupling metrics to its chunks, so a retrieved result arrives
already carrying its structural context.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import networkx as nx
from tqdm import tqdm

from raag_master.chunking import ChunkingConfig, CodeChunk, chunk_snapshot
from raag_master.embeddings import Embedder
from raag_master.vector_store import SearchResult, VectorStore
from raag_tune.ast_types import SnapshotEntry
from raag_tune.metrics import compute_all_metrics

__all__ = ["IndexReport", "index_snapshot", "search_code"]


@dataclass(slots=True)
class IndexReport:
    """What an indexing run did, and what it could not do.

    The skipped counts are reported rather than swallowed. A run that indexed
    four hundred chunks from six hundred files has silently dropped a third of
    the repository, and a caller who does not know that will trust retrieval
    results that were never eligible to be found.
    """

    files_processed: int = 0
    chunks_indexed: int = 0
    files_unreadable: list[str] = field(default_factory=list)
    embedder_name: str = ""
    dimensions: int = 0
    collection: str = ""

    @property
    def files_skipped(self) -> int:
        return len(self.files_unreadable)

    def summary_lines(self) -> list[str]:
        lines = [
            f"Collection    {self.collection}",
            f"Embedder      {self.embedder_name} ({self.dimensions} dimensions)",
            f"Files         {self.files_processed} processed, "
            f"{self.files_skipped} unreadable",
            f"Chunks        {self.chunks_indexed} indexed",
        ]

        if self.files_unreadable:
            preview = ", ".join(self.files_unreadable[:3])
            more = len(self.files_unreadable) - 3
            suffix = f" (and {more} more)" if more > 0 else ""
            lines.append(f"Unreadable    {preview}{suffix}")

        return lines


def _metrics_by_path(graph: nx.DiGraph) -> dict[str, tuple[int, int, float]]:
    """Coupling figures keyed by file path, for payload attachment."""
    return {
        module.path: (
            module.afferent_coupling,
            module.efferent_coupling,
            module.instability,
        )
        for module in compute_all_metrics(graph)
    }


def index_snapshot(
    entries: list[SnapshotEntry],
    graph: nx.DiGraph,
    store: VectorStore,
    embedder: Embedder,
    repo_root: Path,
    *,
    config: ChunkingConfig | None = None,
    recreate: bool = False,
    batch_size: int = 128,
    show_progress: bool = True,
) -> IndexReport:
    """Chunk, embed, and store an entire snapshot.

    Args:
        entries: Parsed files.
        graph: Dependency graph, for the coupling metrics attached to payloads.
        store: Destination collection.
        embedder: Vectoriser. Its dimensions must match the collection's.
        repo_root: Directory the snapshot's relative paths resolve against.
        config: Chunk size bounds.
        recreate: Drop and rebuild the collection first. Use after changing
            embedder or chunking strategy, since vectors produced by different
            models are not comparable even at identical width.
        batch_size: Chunks embedded per call. Batching matters more than it
            looks — per-call model overhead dominates when embedding one text
            at a time.
        show_progress: Print a tqdm bar tracking chunks embedded.
            Semantic embedding is CPU-bound and, on a large repository, slow
            enough that a silent multi-minute wait is indistinguishable from a
            hang. Disabled automatically when stdout is not a terminal — a
            progress bar written to a log file is noise, not information.

    Returns:
        A report of what was indexed and what was skipped.
    """
    if embedder.dimensions != store.dimensions:
        raise ValueError(
            f"embedder produces {embedder.dimensions}-dimensional vectors but the "
            f"collection expects {store.dimensions}. Re-index with --recreate after "
            f"changing embedder."
        )

    chunks, unreadable = chunk_snapshot(entries, repo_root, config)

    store.ensure_collection(recreate=recreate)
    metrics = _metrics_by_path(graph)

    total = 0
    batch_starts = list(range(0, len(chunks), batch_size))

    progress = tqdm(
        batch_starts,
        desc=f"Embedding ({embedder.name})",
        unit="batch",
        disable=not show_progress,
    )

    for start in progress:
        batch: list[CodeChunk] = chunks[start : start + batch_size]
        vectors = embedder.embed([chunk.embedding_text() for chunk in batch])
        total += store.upsert(batch, vectors, metrics)
        progress.set_postfix(chunks=total, refresh=False)

    return IndexReport(
        files_processed=len(entries) - len(unreadable),
        chunks_indexed=total,
        files_unreadable=unreadable,
        embedder_name=embedder.name,
        dimensions=embedder.dimensions,
        collection=store.collection,
    )


def search_code(
    query: str,
    store: VectorStore,
    embedder: Embedder,
    *,
    limit: int = 10,
    paths: list[str] | None = None,
    min_score: float = 0.0,
) -> list[SearchResult]:
    """Embed a query and retrieve matching chunks.

    Passing ``paths`` restricts the search to a specific set of files — the
    hook the blast-radius scoping uses on Day 7. Without it this is ordinary
    semantic search over the whole repository.
    """
    (query_vector,) = embedder.embed([query])
    return store.search(query_vector, limit=limit, paths=paths, min_score=min_score)
