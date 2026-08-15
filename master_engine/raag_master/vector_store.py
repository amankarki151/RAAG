"""Vector storage and structurally-filtered retrieval.

This module is where RAAG stops being a plain RAG system.

Standard retrieval asks "which chunks read like this query?" That is the wrong
question for code. Two files can be worded almost identically and be entirely
unrelated structurally, while the file that will actually break shares no
vocabulary with the query at all.

So every chunk is stored with its file's coupling metrics attached, and search
takes an optional set of file paths to restrict to. Tomorrow the dependency
graph computes that set — the blast radius of a change — and passes it here.
Structural reachability decides what is *eligible*; vector similarity only
decides what ranks highest among the eligible.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from qdrant_client import QdrantClient, models

from raag_master.chunking import CodeChunk

__all__ = ["ChunkPayload", "SearchResult", "VectorStore"]

_NAMESPACE = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")


@dataclass(frozen=True, slots=True)
class ChunkPayload:
    """Metadata stored alongside a chunk's vector.

    The coupling fields are the reason this class exists. Storing them here
    rather than looking them up after retrieval means a search can filter and
    rank on structural properties in the same operation as the similarity
    query, and means a retrieved chunk arrives already carrying the context an
    LLM needs to weigh it.
    """

    path: str
    kind: str
    name: str
    qualified_name: str
    parent_class: str | None
    text: str
    line_start: int
    line_end: int
    afferent_coupling: int = 0
    efferent_coupling: int = 0
    instability: float = 0.0

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "kind": self.kind,
            "name": self.name,
            "qualified_name": self.qualified_name,
            "parent_class": self.parent_class,
            "text": self.text,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "afferent_coupling": self.afferent_coupling,
            "efferent_coupling": self.efferent_coupling,
            "instability": self.instability,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> ChunkPayload:
        return cls(
            path=str(payload.get("path", "")),
            kind=str(payload.get("kind", "")),
            name=str(payload.get("name", "")),
            qualified_name=str(payload.get("qualified_name", "")),
            parent_class=(
                str(payload["parent_class"])
                if payload.get("parent_class") is not None
                else None
            ),
            text=str(payload.get("text", "")),
            line_start=int(str(payload.get("line_start", 0) or 0)),
            line_end=int(str(payload.get("line_end", 0) or 0)),
            afferent_coupling=int(str(payload.get("afferent_coupling", 0) or 0)),
            efferent_coupling=int(str(payload.get("efferent_coupling", 0) or 0)),
            instability=float(str(payload.get("instability", 0.0) or 0.0)),
        )


@dataclass(frozen=True, slots=True)
class SearchResult:
    """One retrieved chunk and how well it matched."""

    score: float
    payload: ChunkPayload

    def citation(self) -> str:
        """Human-followable location, for prompts and reports."""
        return (
            f"{self.payload.path}:{self.payload.line_start}-{self.payload.line_end} "
            f"({self.payload.qualified_name})"
        )


def _point_id(chunk_id: str) -> str:
    """Qdrant point identifier derived from a chunk ID.

    Qdrant accepts integers or UUIDs, not arbitrary strings. A UUID5 is a
    deterministic hash of the chunk ID, so re-indexing unchanged code produces
    the same point and overwrites in place rather than accumulating duplicates.
    """
    return str(uuid.uuid5(_NAMESPACE, chunk_id))


class VectorStore:
    """Thin wrapper over a Qdrant collection.

    Deliberately thin: it owns collection lifecycle, upserting, and filtered
    search, and nothing else. Chunking, embedding, and blast-radius computation
    all live elsewhere, so each can be tested and replaced on its own.
    """

    def __init__(
        self,
        client: QdrantClient,
        collection: str,
        dimensions: int,
        embedder_name: str = "",
    ) -> None:
        self._client = client
        self._collection = collection
        self._dimensions = dimensions
        self._embedder_name = embedder_name

    @classmethod
    def connect(
        cls,
        collection: str,
        dimensions: int,
        *,
        url: str = "http://localhost:6333",
        embedder_name: str = "",
    ) -> VectorStore:
        """Connect to a running Qdrant instance."""
        return cls(QdrantClient(url=url), collection, dimensions, embedder_name)

    @classmethod
    def in_memory(
        cls, collection: str, dimensions: int, embedder_name: str = ""
    ) -> VectorStore:
        """An ephemeral store held entirely in process.

        Used by the test suite. Qdrant's client supports this natively, which
        means the storage layer is tested against the real implementation
        rather than a mock that agrees with whatever the code does.
        """
        return cls(QdrantClient(":memory:"), collection, dimensions, embedder_name)

    @property
    def collection(self) -> str:
        return self._collection

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def ensure_collection(self, *, recreate: bool = False) -> None:
        """Create the collection if absent, optionally replacing it.

        Cosine distance, matching the normalised vectors the embedders produce.

        A ``path`` payload index is created because every structurally-scoped
        search filters on it. Without the index Qdrant scans, and the filter
        that makes this GraphRAG would be the slowest part of every query.
        """
        exists = self._client.collection_exists(self._collection)

        if exists and recreate:
            self._client.delete_collection(self._collection)
            exists = False

        if not exists:
            self._client.create_collection(
                collection_name=self._collection,
                vectors_config=models.VectorParams(
                    size=self._dimensions, distance=models.Distance.COSINE
                ),
            )

        self._client.create_payload_index(
            collection_name=self._collection,
            field_name="path",
            field_schema=models.PayloadSchemaType.KEYWORD,
        )

    def upsert(
        self,
        chunks: Sequence[CodeChunk],
        vectors: Sequence[Sequence[float]],
        metrics_by_path: dict[str, tuple[int, int, float]] | None = None,
        *,
        batch_size: int = 256,
    ) -> int:
        """Store chunks and their vectors.

        Args:
            chunks: The chunks being indexed.
            vectors: One vector per chunk, in the same order.
            metrics_by_path: File path to ``(Ca, Ce, instability)``. Files
                absent from the mapping store zeros — a file with no computed
                metrics is a real case, and refusing to index it would leave
                gaps in retrieval for no benefit.
            batch_size: Points per request. Large repositories produce tens of
                thousands of chunks, and a single request carrying all of them
                risks a timeout or a memory spike on the server.

        Returns:
            Number of points written.
        """
        if len(chunks) != len(vectors):
            raise ValueError(
                f"chunk/vector count mismatch: {len(chunks)} "
                f"chunks, {len(vectors)} vectors"
            )

        metrics_by_path = metrics_by_path or {}
        points: list[models.PointStruct] = []

        for chunk, vector in zip(chunks, vectors, strict=True):
            if len(vector) != self._dimensions:
                raise ValueError(
                    f"vector for {chunk.chunk_id} has {len(vector)} dimensions, "
                    f"collection expects {self._dimensions}"
                )

            afferent, efferent, instability = metrics_by_path.get(
                chunk.path, (0, 0, 0.0)
            )

            payload = ChunkPayload(
                path=chunk.path,
                kind=chunk.kind,
                name=chunk.name,
                qualified_name=chunk.qualified_name,
                parent_class=chunk.parent_class,
                text=chunk.text,
                line_start=chunk.line_start,
                line_end=chunk.line_end,
                afferent_coupling=afferent,
                efferent_coupling=efferent,
                instability=instability,
            )

            points.append(
                models.PointStruct(
                    id=_point_id(chunk.chunk_id),
                    vector=list(vector),
                    payload=payload.to_dict(),
                )
            )

        for start in range(0, len(points), batch_size):
            self._client.upsert(
                collection_name=self._collection,
                points=points[start : start + batch_size],
                wait=True,
            )

        return len(points)

    def search(
        self,
        query_vector: Sequence[float],
        *,
        limit: int = 10,
        paths: Iterable[str] | None = None,
        min_score: float = 0.0,
    ) -> list[SearchResult]:
        """Retrieve the closest chunks, optionally restricted to given files.

        ``paths`` is the parameter that makes this GraphRAG. When the caller
        passes a blast radius computed from the dependency graph, similarity
        ranks only within the set of files a change can actually reach.
        Everything outside is not down-weighted — it is not considered, because
        it cannot be affected regardless of how similar it reads.

        Args:
            query_vector: The embedded query.
            limit: Maximum results.
            paths: Restrict to these files. ``None`` searches everything.
            min_score: Drop results below this similarity.
        """
        query_filter: models.Filter | None = None

        if paths is not None:
            path_list = list(paths)
            if not path_list:
                # An empty blast radius means nothing is reachable. Searching
                # unfiltered here would silently return the whole repository,
                # which is the opposite of what was asked for.
                return []

            query_filter = models.Filter(
                must=[
                    models.FieldCondition(
                        key="path", match=models.MatchAny(any=path_list)
                    )
                ]
            )

        response = self._client.query_points(
            collection_name=self._collection,
            query=list(query_vector),
            query_filter=query_filter,
            limit=limit,
            score_threshold=min_score or None,
            with_payload=True,
        )

        return [
            SearchResult(
                score=float(point.score),
                payload=ChunkPayload.from_dict(dict(point.payload or {})),
            )
            for point in response.points
        ]

    def count(self) -> int:
        """Points currently stored."""
        return int(self._client.count(self._collection, exact=True).count)

    def delete_paths(self, paths: Sequence[str]) -> None:
        """Remove every chunk belonging to the given files.

        Needed for incremental re-indexing: a deleted or renamed file leaves
        vectors behind that would otherwise still be retrieved as current code.
        """
        if not paths:
            return

        self._client.delete(
            collection_name=self._collection,
            points_selector=models.FilterSelector(
                filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="path", match=models.MatchAny(any=list(paths))
                        )
                    ]
                )
            ),
            wait=True,
        )
