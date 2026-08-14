"""Tests for dependency edge extraction.

Snapshot entries are built directly here rather than parsed from source. The
extractor's contract is with the AST, not with any particular grammar, and
constructing arenas by hand keeps each test's input visible next to its
assertion.
"""

from __future__ import annotations

from raag_tune.ast_types import AstArena, AstNode, AstNodeKind, SnapshotEntry
from raag_tune.edge_extractor import extract_dependencies
from raag_tune.graph_types import EdgeKind, Resolution


def node(
    kind: AstNodeKind,
    name: str = "",
    *,
    parent: int = 0,
) -> AstNode:
    return AstNode(
        kind=kind,
        name=name,
        byte_start=0,
        byte_end=1,
        first_child_index=0,
        child_count=0,
        parent_index=parent,
    )


def file_entry(path: str, *children: AstNode) -> SnapshotEntry:
    """A file whose root has the given nodes as children."""
    root = AstNode(
        kind=AstNodeKind.FILE,
        name="",
        byte_start=0,
        byte_end=100,
        first_child_index=1,
        child_count=len(children),
        parent_index=-1,
    )
    return SnapshotEntry(path=path, arena=AstArena(nodes=[root, *children]))


# --- Imports -----------------------------------------------------------------


def test_resolved_cpp_include_becomes_an_edge() -> None:
    entries = [
        file_entry("src/engine.cpp", node(AstNodeKind.IMPORT, "engine.hpp")),
        file_entry("src/engine.hpp"),
    ]

    report = extract_dependencies(entries)

    assert len(report.dependencies) == 1
    dependency = report.dependencies[0]
    assert dependency.source == "src/engine.cpp"
    assert dependency.target == "src/engine.hpp"
    assert dependency.kind is EdgeKind.IMPORT
    assert dependency.resolution is Resolution.RESOLVED


def test_external_include_produces_no_edge() -> None:
    entries = [file_entry("src/engine.cpp", node(AstNodeKind.IMPORT, "vector"))]

    report = extract_dependencies(entries)

    assert report.dependencies == []
    assert report.resolution_counts[Resolution.EXTERNAL] == 1
    assert report.unresolved_targets["vector"] == 1


def test_python_import_resolves_to_module() -> None:
    entries = [
        file_entry("tools/run.py", node(AstNodeKind.IMPORT, "pkg.core")),
        file_entry("pkg/core.py"),
    ]

    report = extract_dependencies(entries)

    assert len(report.dependencies) == 1
    assert report.dependencies[0].target == "pkg/core.py"


def test_raw_target_is_preserved_for_diagnostics() -> None:
    """An unresolved import is far easier to investigate with the original text."""
    entries = [
        file_entry("src/a.cpp", node(AstNodeKind.IMPORT, "b.hpp")),
        file_entry("src/b.hpp"),
    ]

    report = extract_dependencies(entries)

    assert report.dependencies[0].raw_target == "b.hpp"


def test_self_import_is_dropped() -> None:
    """A file importing itself is a parse artefact, and a self-loop would
    distort every degree-based metric downstream."""
    entries = [file_entry("src/a.hpp", node(AstNodeKind.IMPORT, "a.hpp"))]

    report = extract_dependencies(entries)

    assert report.dependencies == []


def test_import_node_without_a_name_is_ignored() -> None:
    """Absent names mean extraction failed, not that a dependency exists."""
    entries = [
        file_entry("src/a.cpp", node(AstNodeKind.IMPORT, "")),
        file_entry("src/b.hpp"),
    ]

    report = extract_dependencies(entries)

    assert report.dependencies == []
    assert report.total == 0


def test_repeated_import_counts_twice() -> None:
    entries = [
        file_entry(
            "src/a.cpp",
            node(AstNodeKind.IMPORT, "b.hpp"),
            node(AstNodeKind.IMPORT, "b.hpp"),
        ),
        file_entry("src/b.hpp"),
    ]

    report = extract_dependencies(entries)

    assert len(report.dependencies) == 2


# --- Inheritance -------------------------------------------------------------


def test_inheritance_creates_an_edge_to_the_base_file() -> None:
    entries = [
        file_entry("src/derived.hpp", node(AstNodeKind.CLASS, "Derived : Base")),
        file_entry("src/base.hpp", node(AstNodeKind.CLASS, "Base")),
    ]

    report = extract_dependencies(entries)

    inheritance = [
        dep for dep in report.dependencies if dep.kind is EdgeKind.INHERITANCE
    ]
    assert len(inheritance) == 1
    assert inheritance[0].source == "src/derived.hpp"
    assert inheritance[0].target == "src/base.hpp"


def test_multiple_inheritance_creates_one_edge_per_base() -> None:
    entries = [
        file_entry("src/d.hpp", node(AstNodeKind.CLASS, "D : A,B")),
        file_entry("src/a.hpp", node(AstNodeKind.CLASS, "A")),
        file_entry("src/b.hpp", node(AstNodeKind.CLASS, "B")),
    ]

    report = extract_dependencies(entries)

    targets = {
        dep.target for dep in report.dependencies if dep.kind is EdgeKind.INHERITANCE
    }
    assert targets == {"src/a.hpp", "src/b.hpp"}


def test_qualified_base_name_matches_on_final_component() -> None:
    entries = [
        file_entry("src/d.hpp", node(AstNodeKind.CLASS, "D : ns::Base")),
        file_entry("src/base.hpp", node(AstNodeKind.CLASS, "Base")),
    ]

    report = extract_dependencies(entries)

    assert any(dep.kind is EdgeKind.INHERITANCE for dep in report.dependencies)


def test_ambiguous_base_name_produces_no_edge() -> None:
    """A base defined in two files cannot be attributed without guessing, and a
    wrong inheritance edge implies coupling that does not exist."""
    entries = [
        file_entry("src/d.hpp", node(AstNodeKind.CLASS, "D : Base")),
        file_entry("src/a.hpp", node(AstNodeKind.CLASS, "Base")),
        file_entry("src/b.hpp", node(AstNodeKind.CLASS, "Base")),
    ]

    report = extract_dependencies(entries)

    assert not any(dep.kind is EdgeKind.INHERITANCE for dep in report.dependencies)


def test_unknown_base_produces_no_edge() -> None:
    entries = [file_entry("src/d.hpp", node(AstNodeKind.CLASS, "D : Missing"))]

    report = extract_dependencies(entries)

    assert report.dependencies == []


def test_class_without_bases_produces_no_edge() -> None:
    entries = [
        file_entry("src/a.hpp", node(AstNodeKind.CLASS, "Standalone")),
        file_entry("src/b.hpp", node(AstNodeKind.CLASS, "Other")),
    ]

    report = extract_dependencies(entries)

    assert report.dependencies == []


def test_self_inheritance_is_dropped() -> None:
    entries = [
        file_entry(
            "src/a.hpp",
            node(AstNodeKind.CLASS, "Derived : Base"),
            node(AstNodeKind.CLASS, "Base"),
        )
    ]

    report = extract_dependencies(entries)

    assert report.dependencies == []


# --- Reporting ---------------------------------------------------------------


def test_report_counts_each_resolution_kind() -> None:
    entries = [
        file_entry(
            "src/a.cpp",
            node(AstNodeKind.IMPORT, "b.hpp"),
            node(AstNodeKind.IMPORT, "vector"),
        ),
        file_entry("src/b.hpp"),
    ]

    report = extract_dependencies(entries)

    assert report.resolution_counts[Resolution.RESOLVED] == 1
    assert report.resolution_counts[Resolution.EXTERNAL] == 1
    assert report.total == 2


def test_resolved_ratio() -> None:
    entries = [
        file_entry(
            "src/a.cpp",
            node(AstNodeKind.IMPORT, "b.hpp"),
            node(AstNodeKind.IMPORT, "c.hpp"),
            node(AstNodeKind.IMPORT, "vector"),
            node(AstNodeKind.IMPORT, "string"),
        ),
        file_entry("src/b.hpp"),
        file_entry("src/c.hpp"),
    ]

    report = extract_dependencies(entries)

    assert report.resolved_ratio == 0.5


def test_resolved_ratio_of_empty_report_is_zero() -> None:
    """Guards the division; an empty repository must not raise."""
    report = extract_dependencies([])

    assert report.resolved_ratio == 0.0
    assert report.total == 0


def test_summary_line_reports_every_category() -> None:
    entries = [
        file_entry("src/a.cpp", node(AstNodeKind.IMPORT, "vector")),
    ]

    line = extract_dependencies(entries).summary_line()

    assert "1 imports" in line
    assert "external" in line


def test_unresolved_targets_are_counted_by_frequency() -> None:
    entries = [
        file_entry(
            "src/a.cpp",
            node(AstNodeKind.IMPORT, "vector"),
            node(AstNodeKind.IMPORT, "vector"),
            node(AstNodeKind.IMPORT, "string"),
        )
    ]

    report = extract_dependencies(entries)

    assert report.unresolved_targets.most_common(1) == [("vector", 2)]


def test_empty_snapshot_produces_empty_report() -> None:
    report = extract_dependencies([])

    assert report.dependencies == []
    assert report.total == 0
