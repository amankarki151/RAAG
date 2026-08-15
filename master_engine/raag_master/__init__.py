"""RAAG Master Engine — retrieval and AI orchestration.

Chunks code at AST boundaries, embeds it, and stores it with the structural
metrics that let retrieval be scoped by dependency reachability rather than by
text similarity alone.
"""

from raag_master.chunking import (
    ChunkingConfig,
    CodeChunk,
    chunk_entry,
    chunk_snapshot,
)
from raag_master.embeddings import (
    Embedder,
    FastEmbedEmbedder,
    HashEmbedder,
    default_embedder,
)
from raag_master.indexer import IndexReport, index_snapshot, search_code
from raag_master.vector_store import ChunkPayload, SearchResult, VectorStore

__version__ = "0.1.0"

__all__ = [
    "ChunkPayload",
    "ChunkingConfig",
    "CodeChunk",
    "Embedder",
    "FastEmbedEmbedder",
    "HashEmbedder",
    "IndexReport",
    "SearchResult",
    "VectorStore",
    "chunk_entry",
    "chunk_snapshot",
    "default_embedder",
    "index_snapshot",
    "search_code",
]
