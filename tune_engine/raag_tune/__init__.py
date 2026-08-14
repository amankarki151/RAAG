"""RAAG Tune Engine — dependency graph construction and software metrics.

Consumes the binary AST snapshots produced by the Sample Engine and turns them
into a quantified dependency graph.
"""

from raag_tune.ast_types import AstArena, AstNode, AstNodeKind, SnapshotEntry
from raag_tune.dependency_graph import (
    GraphSummary,
    build_dependency_graph,
    find_cycles,
    summarise_graph,
    topological_layers,
)
from raag_tune.edge_extractor import ExtractionReport, extract_dependencies
from raag_tune.graph_types import Dependency, EdgeKind, ModuleInfo, Resolution
from raag_tune.module_index import ModuleIndex, ResolutionOutcome
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
    "Dependency",
    "EdgeKind",
    "ExtractionReport",
    "GraphSummary",
    "InvalidMagicError",
    "ModuleIndex",
    "ModuleInfo",
    "Resolution",
    "ResolutionOutcome",
    "SnapshotEntry",
    "SnapshotError",
    "TruncatedSnapshotError",
    "UnsupportedSchemaVersionError",
    "build_dependency_graph",
    "extract_dependencies",
    "find_cycles",
    "read_snapshot",
    "read_snapshot_header",
    "summarise_graph",
    "topological_layers",
]
