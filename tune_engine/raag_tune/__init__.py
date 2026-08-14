"""RAAG Tune Engine — dependency graph construction and software metrics.

Consumes the binary AST snapshots produced by the Sample Engine and turns them
into a quantified dependency graph.
"""

from raag_tune.ast_types import AstArena, AstNode, AstNodeKind, SnapshotEntry
from raag_tune.snapshot_reader import (
    SUPPORTED_SCHEMA_VERSION,
    InvalidMagicError,
    SnapshotError,
    TruncatedSnapshotError,
    UnsupportedSchemaVersionError,
    read_snapshot,
    read_snapshot_header,
)

__version__ = "0.1.0"

__all__ = [
    "SUPPORTED_SCHEMA_VERSION",
    "AstArena",
    "AstNode",
    "AstNodeKind",
    "InvalidMagicError",
    "SnapshotEntry",
    "SnapshotError",
    "TruncatedSnapshotError",
    "UnsupportedSchemaVersionError",
    "read_snapshot",
    "read_snapshot_header",
]
