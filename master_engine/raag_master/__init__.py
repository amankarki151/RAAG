"""RAAG Master Engine — retrieval and AI orchestration.

Chunks code at AST boundaries, embeds it, stores it with structural metrics,
and scopes AI-assisted refactoring to the blast radius the dependency graph
says a change can actually reach.
"""

from raag_master.audit import AuditLog, AuditRecord
from raag_master.blast_radius import BlastRadius, compute_blast_radius
from raag_master.chunking import (
    ChunkingConfig,
    CodeChunk,
    chunk_entry,
    chunk_snapshot,
)
from raag_master.context import (
    SYSTEM_PROMPT,
    AssembledContext,
    ContextBudget,
    assemble_context,
)
from raag_master.embeddings import (
    Embedder,
    FastEmbedEmbedder,
    HashEmbedder,
    default_embedder,
)
from raag_master.indexer import IndexReport, index_snapshot, search_code
from raag_master.pipeline import RefactorOutcome, run_refactor
from raag_master.reasoning import (
    AnthropicReasoner,
    DryRunReasoner,
    Reasoner,
    ReasoningResult,
    default_model,
)
from raag_master.vector_store import ChunkPayload, SearchResult, VectorStore

__version__ = "0.2.0"

__all__ = [
    "SYSTEM_PROMPT",
    "AnthropicReasoner",
    "AssembledContext",
    "AuditLog",
    "AuditRecord",
    "BlastRadius",
    "ChunkPayload",
    "ChunkingConfig",
    "CodeChunk",
    "ContextBudget",
    "DryRunReasoner",
    "Embedder",
    "FastEmbedEmbedder",
    "HashEmbedder",
    "IndexReport",
    "Reasoner",
    "ReasoningResult",
    "RefactorOutcome",
    "SearchResult",
    "VectorStore",
    "assemble_context",
    "chunk_entry",
    "chunk_snapshot",
    "compute_blast_radius",
    "default_embedder",
    "default_model",
    "index_snapshot",
    "run_refactor",
    "search_code",
]
