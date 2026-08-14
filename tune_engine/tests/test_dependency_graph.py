"""Tests for dependency graph construction, cycle detection, and layering.

Graphs are built from hand-written snapshot entries so each test's topology is
visible in the test itself. Where a graph shape matters — a cycle, a diamond, a
disconnected node — it is stated explicitly rather than inferred from a real
repository that might change.
"""

from __future__ import annotations

import networkx as nx

from raag_tune.ast_types import AstArena, AstNode, AstNodeKind, SnapshotEntry
from raag_tune.dependency_graph import (
    build_dependency_graph,
    find_cycles,
    summarise_graph,
    topological_layers,
)
from raag_tune.graph_types import EdgeKind


def imports(*targets: str) -> list[AstNode]:
    return [
        AstNode(
            kind=AstNodeKind.IMPORT,
            name=target,
            byte_start=0,
            byte_end=1,
            first_child_index=0,
            child_count=0,
            parent_index=0,
        )
        for target in targets
    ]


def file_entry(path: str, *children: AstNode) -> SnapshotEntry:
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


def linear_repo() -> list[SnapshotEntry]:
    """a -> b -> c, a straight dependency chain."""
    return [
        file_entry("a.hpp", *imports("b.hpp")),
        file_entry("b.hpp", *imports("c.hpp")),
        file_entry("c.hpp"),
    ]


def cyclic_repo() -> list[SnapshotEntry]:
    """a -> b -> c -> a, plus an unrelated file."""
    return [
        file_entry("a.hpp", *imports("b.hpp")),
        file_entry("b.hpp", *imports("c.hpp")),
        file_entry("c.hpp", *imports("a.hpp")),
        file_entry("lonely.hpp"),
    ]


# --- Construction ------------------------------------------------------------


def test_every_parsed_file_becomes_a_node() -> None:
    """Including files with no dependencies either way.

    Dropping them would flatter the graph: a file nothing depends on is a real
    fact about a codebase, not an absence of data.
    """
    graph, _ = build_dependency_graph(cyclic_repo())

    assert set(graph.nodes) == {"a.hpp", "b.hpp", "c.hpp", "lonely.hpp"}


def test_resolved_imports_become_edges() -> None:
    graph, _ = build_dependency_graph(linear_repo())

    assert graph.has_edge("a.hpp", "b.hpp")
    assert graph.has_edge("b.hpp", "c.hpp")
    assert graph.number_of_edges() == 2


def test_edge_direction_runs_from_dependent_to_dependency() -> None:
    """Direction is the whole basis of afferent versus efferent coupling.

    Reversing it inverts every metric computed on Day 5 while leaving the graph
    superficially plausible.
    """
    graph, _ = build_dependency_graph(linear_repo())

    assert graph.has_edge("a.hpp", "b.hpp")
    assert not graph.has_edge("b.hpp", "a.hpp")


def test_node_attributes_carry_ast_counts() -> None:
    entry = file_entry(
        "a.cpp",
        *imports("b.hpp"),
        AstNode(
            kind=AstNodeKind.CLASS,
            name="Widget",
            byte_start=0,
            byte_end=1,
            first_child_index=0,
            child_count=0,
            parent_index=0,
        ),
    )
    graph, _ = build_dependency_graph([entry, file_entry("b.hpp")])

    attributes = graph.nodes["a.cpp"]
    assert attributes["class_count"] == 1
    assert attributes["import_count"] == 1
    assert attributes["node_count"] == 3


def test_parallel_dependencies_collapse_into_one_weighted_edge() -> None:
    """Two files importing each other twice are not more coupled than once.

    The relationship exists or it does not; the count is kept as a weight for
    callers that want it.
    """
    entries = [
        file_entry("a.cpp", *imports("b.hpp", "b.hpp", "b.hpp")),
        file_entry("b.hpp"),
    ]
    graph, _ = build_dependency_graph(entries)

    assert graph.number_of_edges() == 1
    assert graph.edges["a.cpp", "b.hpp"]["weight"] == 3


def test_edge_records_every_relationship_kind() -> None:
    entries = [
        file_entry(
            "derived.hpp",
            *imports("base.hpp"),
            AstNode(
                kind=AstNodeKind.CLASS,
                name="Derived : Base",
                byte_start=0,
                byte_end=1,
                first_child_index=0,
                child_count=0,
                parent_index=0,
            ),
        ),
        file_entry(
            "base.hpp",
            AstNode(
                kind=AstNodeKind.CLASS,
                name="Base",
                byte_start=0,
                byte_end=1,
                first_child_index=0,
                child_count=0,
                parent_index=0,
            ),
        ),
    ]
    graph, _ = build_dependency_graph(entries)

    kinds = graph.edges["derived.hpp", "base.hpp"]["kinds"]
    assert EdgeKind.IMPORT in kinds
    assert EdgeKind.INHERITANCE in kinds


def test_external_imports_do_not_add_nodes() -> None:
    """Third-party dependencies are counted, not graphed.

    Adding them would inflate node counts with code the repository does not own
    and distort every density and coupling figure.
    """
    entries = [file_entry("a.cpp", *imports("vector", "string"))]
    graph, report = build_dependency_graph(entries)

    assert set(graph.nodes) == {"a.cpp"}
    assert graph.number_of_edges() == 0
    assert report.total == 2


def test_empty_snapshot_produces_empty_graph() -> None:
    graph, _ = build_dependency_graph([])

    assert graph.number_of_nodes() == 0
    assert graph.number_of_edges() == 0


def test_passing_a_precomputed_report_avoids_repeating_work() -> None:
    from raag_tune.edge_extractor import extract_dependencies

    entries = linear_repo()
    report = extract_dependencies(entries)

    graph, returned = build_dependency_graph(entries, report=report)

    assert returned is report
    assert graph.number_of_edges() == 2


# --- Cycle detection ---------------------------------------------------------


def test_acyclic_graph_has_no_cycles() -> None:
    graph, _ = build_dependency_graph(linear_repo())

    assert find_cycles(graph) == []


def test_cycle_is_detected_with_all_its_members() -> None:
    graph, _ = build_dependency_graph(cyclic_repo())

    cycles = find_cycles(graph)

    assert len(cycles) == 1
    assert cycles[0] == ["a.hpp", "b.hpp", "c.hpp"]


def test_two_node_cycle_is_detected() -> None:
    entries = [
        file_entry("a.hpp", *imports("b.hpp")),
        file_entry("b.hpp", *imports("a.hpp")),
    ]
    graph, _ = build_dependency_graph(entries)

    assert find_cycles(graph) == [["a.hpp", "b.hpp"]]


def test_cycles_are_returned_largest_first() -> None:
    entries = [
        file_entry("a.hpp", *imports("b.hpp")),
        file_entry("b.hpp", *imports("c.hpp")),
        file_entry("c.hpp", *imports("a.hpp")),
        file_entry("x.hpp", *imports("y.hpp")),
        file_entry("y.hpp", *imports("x.hpp")),
    ]
    graph, _ = build_dependency_graph(entries)

    cycles = find_cycles(graph)

    assert len(cycles) == 2
    assert len(cycles[0]) == 3
    assert len(cycles[1]) == 2


def test_cycle_limit_is_respected() -> None:
    entries = [
        file_entry("a.hpp", *imports("b.hpp")),
        file_entry("b.hpp", *imports("a.hpp")),
        file_entry("x.hpp", *imports("y.hpp")),
        file_entry("y.hpp", *imports("x.hpp")),
    ]
    graph, _ = build_dependency_graph(entries)

    assert len(find_cycles(graph, limit=1)) == 1


def test_diamond_dependency_is_not_a_cycle() -> None:
    """a depends on b and c, both of which depend on d. No cycle."""
    entries = [
        file_entry("a.hpp", *imports("b.hpp", "c.hpp")),
        file_entry("b.hpp", *imports("d.hpp")),
        file_entry("c.hpp", *imports("d.hpp")),
        file_entry("d.hpp"),
    ]
    graph, _ = build_dependency_graph(entries)

    assert find_cycles(graph) == []


# --- Layering ----------------------------------------------------------------


def test_layers_place_foundations_first() -> None:
    graph, _ = build_dependency_graph(linear_repo())

    layers = topological_layers(graph)

    assert layers == [["c.hpp"], ["b.hpp"], ["a.hpp"]]


def test_independent_files_share_a_layer() -> None:
    entries = [
        file_entry("a.hpp", *imports("base.hpp")),
        file_entry("b.hpp", *imports("base.hpp")),
        file_entry("base.hpp"),
    ]
    graph, _ = build_dependency_graph(entries)

    layers = topological_layers(graph)

    assert layers[0] == ["base.hpp"]
    assert layers[1] == ["a.hpp", "b.hpp"]


def test_layering_survives_cycles() -> None:
    """A tangled repository must still produce a usable layering.

    Condensing each strongly connected component to one vertex makes the graph
    acyclic, so the tangle appears as one wide layer rather than as a failure.
    """
    graph, _ = build_dependency_graph(cyclic_repo())

    layers = topological_layers(graph)

    flattened = [path for layer in layers for path in layer]
    assert sorted(flattened) == ["a.hpp", "b.hpp", "c.hpp", "lonely.hpp"]

    cycle_layer = next(layer for layer in layers if "a.hpp" in layer)
    assert set(cycle_layer) >= {"a.hpp", "b.hpp", "c.hpp"}


def test_every_file_appears_in_exactly_one_layer() -> None:
    graph, _ = build_dependency_graph(cyclic_repo())

    layers = topological_layers(graph)
    flattened = [path for layer in layers for path in layer]

    assert len(flattened) == len(set(flattened)) == graph.number_of_nodes()


def test_empty_graph_has_no_layers() -> None:
    assert topological_layers(nx.DiGraph()) == []


# --- Summary -----------------------------------------------------------------


def test_summary_counts_nodes_and_edges() -> None:
    graph, _ = build_dependency_graph(linear_repo())

    summary = summarise_graph(graph)

    assert summary.node_count == 3
    assert summary.edge_count == 2
    assert summary.import_edges == 2
    assert summary.inheritance_edges == 0


def test_summary_reports_cycles() -> None:
    graph, _ = build_dependency_graph(cyclic_repo())

    summary = summarise_graph(graph)

    assert summary.cycle_count == 1
    assert summary.largest_cycle == 3


def test_summary_counts_isolated_files() -> None:
    graph, _ = build_dependency_graph(cyclic_repo())

    assert summarise_graph(graph).isolated_files == 1


def test_summary_of_empty_graph_does_not_raise() -> None:
    summary = summarise_graph(nx.DiGraph())

    assert summary.node_count == 0
    assert summary.cycle_count == 0
    assert summary.largest_cycle == 0


def test_report_lines_mention_every_headline_figure() -> None:
    graph, _ = build_dependency_graph(cyclic_repo())

    text = "\n".join(summarise_graph(graph).report_lines())

    assert "Files" in text
    assert "Dependencies" in text
    assert "Cycles" in text
    assert "Dependency layers" in text
