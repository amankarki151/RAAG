"""RAAG Tune Engine — dependency graph construction and software metrics.

Consumes the binary AST snapshots produced by the Sample Engine and turns them
into a quantified dependency graph with coupling and cohesion metrics.
"""

from raag_tune.ast_types import AstArena, AstNode, AstNodeKind, SnapshotEntry
from raag_tune.cohesion import ClassCohesion, compute_cohesion, compute_file_cohesion
from raag_tune.dependency_graph import (
    GraphSummary,
    build_dependency_graph,
    find_cycles,
    summarise_graph,
    topological_layers,
)
from raag_tune.edge_extractor import ExtractionReport, extract_dependencies
from raag_tune.graph_types import Dependency, EdgeKind, ModuleInfo, Resolution
from raag_tune.metrics import (
    ModuleMetrics,
    compute_all_metrics,
    compute_module_metrics,
    instability,
)
from raag_tune.module_index import ModuleIndex, ResolutionOutcome
from raag_tune.report import (
    MetricsReport,
    Severity,
    Thresholds,
    Violation,
    build_report,
)
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
    "ClassCohesion",
    "Dependency",
    "EdgeKind",
    "ExtractionReport",
    "GraphSummary",
    "InvalidMagicError",
    "MetricsReport",
    "ModuleIndex",
    "ModuleInfo",
    "ModuleMetrics",
    "Resolution",
    "ResolutionOutcome",
    "Severity",
    "SnapshotEntry",
    "SnapshotError",
    "Thresholds",
    "TruncatedSnapshotError",
    "UnsupportedSchemaVersionError",
    "Violation",
    "build_dependency_graph",
    "build_report",
    "compute_all_metrics",
    "compute_cohesion",
    "compute_file_cohesion",
    "compute_module_metrics",
    "extract_dependencies",
    "find_cycles",
    "instability",
    "read_snapshot",
    "read_snapshot_header",
    "summarise_graph",
    "topological_layers",
]
