"""Extraction of dependency edges from parsed ASTs.

Walks each file's arena, finds the nodes that express a dependency, and turns
them into resolved :class:`Dependency` records.

Scope note: only import and inheritance edges are extracted. Call edges are
deliberately absent. Tree-sitter reports that a call happens and what text names
the callee, but resolving that text to a definition requires knowing overload
sets, virtual dispatch targets, and namespace lookup — none of which survive in
a parse tree. Emitting call edges from name matching alone would produce a graph
that looks richer and is measurably less correct, and every metric computed from
it would inherit the error.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from raag_tune.ast_types import AstNodeKind, SnapshotEntry
from raag_tune.graph_types import Dependency, EdgeKind, Resolution
from raag_tune.module_index import ModuleIndex

__all__ = ["ExtractionReport", "extract_dependencies"]

_PYTHON_SUFFIXES = frozenset({".py", ".pyi"})


@dataclass(slots=True)
class ExtractionReport:
    """Dependencies found, alongside how confident we are in them.

    The counts are not decoration. A graph built from imports of which half went
    unresolved describes something quite different from one where nearly all
    resolved, and a caller cannot judge the metrics without knowing which it has.
    """

    dependencies: list[Dependency] = field(default_factory=list)
    resolution_counts: Counter[Resolution] = field(default_factory=Counter)
    unresolved_targets: Counter[str] = field(default_factory=Counter)

    @property
    def total(self) -> int:
        return sum(self.resolution_counts.values())

    @property
    def resolved_ratio(self) -> float:
        """Share of imports matched to a file in the repository.

        Ambiguous matches count as resolved: a target was chosen and an edge
        exists. They are reported separately so a low-confidence graph is
        visible rather than implied.
        """
        if self.total == 0:
            return 0.0
        internal = (
            self.resolution_counts[Resolution.RESOLVED]
            + self.resolution_counts[Resolution.AMBIGUOUS]
        )
        return internal / self.total

    def summary_line(self) -> str:
        return (
            f"{self.total} imports: "
            f"{self.resolution_counts[Resolution.RESOLVED]} resolved, "
            f"{self.resolution_counts[Resolution.AMBIGUOUS]} ambiguous, "
            f"{self.resolution_counts[Resolution.EXTERNAL]} external "
            f"({self.resolved_ratio:.1%} internal)"
        )


def _is_python(path: str) -> bool:
    return PurePosixPath(path).suffix in _PYTHON_SUFFIXES


def _split_base_types(class_name: str) -> tuple[str, list[str]]:
    """Split a class node's name into its own name and its base types.

    The Sample Engine encodes inheritance as ``Derived : Base1,Base2`` because
    the snapshot format carries one string per node. Widening the format for a
    case this narrow was not worth a schema break; splitting here is the cost
    of that decision, and it is confined to one function.
    """
    if " : " not in class_name:
        return class_name, []

    own, _, bases = class_name.partition(" : ")
    return own, [base.strip() for base in bases.split(",") if base.strip()]


def extract_dependencies(entries: list[SnapshotEntry]) -> ExtractionReport:
    """Extract every dependency the snapshot expresses.

    Args:
        entries: Parsed files, as returned by ``read_snapshot``.

    Returns:
        The dependencies found and a breakdown of how they resolved.
    """
    index = ModuleIndex.from_paths([entry.path for entry in entries])
    report = ExtractionReport()

    # Class name to defining file, for inheritance edges. Built in a first pass
    # because a base class is frequently declared in a file processed later.
    class_locations = _index_class_definitions(entries)

    for entry in entries:
        _extract_imports(entry, index, report)
        _extract_inheritance(entry, class_locations, report)

    return report


def _index_class_definitions(entries: list[SnapshotEntry]) -> dict[str, list[str]]:
    locations: dict[str, list[str]] = {}

    for entry in entries:
        for node in entry.arena:
            if node.kind is not AstNodeKind.CLASS or not node.name:
                continue
            own_name, _ = _split_base_types(node.name)
            if own_name:
                locations.setdefault(own_name, []).append(entry.path)

    return locations


def _extract_imports(
    entry: SnapshotEntry, index: ModuleIndex, report: ExtractionReport
) -> None:
    python = _is_python(entry.path)

    for node in entry.arena:
        if node.kind is not AstNodeKind.IMPORT or not node.name:
            continue

        outcome = (
            index.resolve_python_import(node.name, entry.path)
            if python
            else index.resolve_cpp_include(node.name, entry.path)
        )

        report.resolution_counts[outcome.resolution] += 1

        if outcome.resolution is Resolution.EXTERNAL:
            report.unresolved_targets[node.name] += 1
            continue

        # A file importing itself is a parse artefact, not a dependency, and a
        # self-loop would distort every degree-based metric downstream.
        if outcome.target == entry.path:
            continue

        report.dependencies.append(
            Dependency(
                source=entry.path,
                target=outcome.target,
                kind=EdgeKind.IMPORT,
                resolution=outcome.resolution,
                raw_target=node.name,
            )
        )


def _extract_inheritance(
    entry: SnapshotEntry,
    class_locations: dict[str, list[str]],
    report: ExtractionReport,
) -> None:
    for node in entry.arena:
        if node.kind is not AstNodeKind.CLASS or not node.name:
            continue

        _, bases = _split_base_types(node.name)

        for base in bases:
            # Qualified names are reduced to their final component. Full
            # namespace resolution needs a symbol table; the last segment is
            # the best available approximation from syntax alone.
            simple_name = base.rsplit("::", 1)[-1].rsplit(".", 1)[-1].strip()
            defining_files = class_locations.get(simple_name)

            if not defining_files:
                continue

            # A base name defined in several files cannot be attributed to one
            # of them without guessing, and a wrong inheritance edge implies
            # coupling that does not exist. Skipped rather than picked.
            if len(defining_files) > 1:
                continue

            target = defining_files[0]
            if target == entry.path:
                continue

            report.dependencies.append(
                Dependency(
                    source=entry.path,
                    target=target,
                    kind=EdgeKind.INHERITANCE,
                    resolution=Resolution.RESOLVED,
                    raw_target=base,
                )
            )
