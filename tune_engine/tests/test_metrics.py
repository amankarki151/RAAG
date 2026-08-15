"""Tests for coupling and instability metrics.

Every expected value here is hand-calculated from the formula rather than
copied from a run. A metric test that asserts what the code currently produces
verifies nothing; it just freezes today's bug into a passing suite.
"""

from __future__ import annotations

import networkx as nx
import pytest

from raag_tune.metrics import (
    ModuleMetrics,
    compute_all_metrics,
    compute_module_metrics,
    instability,
)


def graph_from_edges(*edges: tuple[str, str]) -> nx.DiGraph:
    """Build a graph with the node attributes the metrics layer expects."""
    graph = nx.DiGraph()
    for source, target in edges:
        for node in (source, target):
            if node not in graph:
                graph.add_node(node, node_count=0, class_count=0, function_count=0)
        graph.add_edge(source, target)
    return graph


# --- The formula -------------------------------------------------------------


@pytest.mark.parametrize(
    ("afferent", "efferent", "expected"),
    [
        (0, 0, 0.0),  # Isolated: convention, not a measurement.
        (5, 0, 0.0),  # Maximally stable.
        (0, 5, 1.0),  # Maximally unstable.
        (5, 5, 0.5),  # Balanced.
        (1, 3, 0.75),
        (3, 1, 0.25),
        (2, 8, 0.8),
    ],
)
def test_instability_formula(afferent: int, efferent: int, expected: float) -> None:
    assert instability(afferent, efferent) == pytest.approx(expected)


def test_instability_never_leaves_the_unit_interval() -> None:
    for afferent in range(0, 30, 3):
        for efferent in range(0, 30, 3):
            assert 0.0 <= instability(afferent, efferent) <= 1.0


def test_zero_degree_does_not_divide_by_zero() -> None:
    """The guard exists because an isolated file is common, not exotic."""
    assert instability(0, 0) == 0.0


# --- Per-module computation --------------------------------------------------


def test_afferent_coupling_counts_incoming_edges() -> None:
    graph = graph_from_edges(("a.cpp", "core.hpp"), ("b.cpp", "core.hpp"))

    metrics = compute_module_metrics(graph, "core.hpp")

    assert metrics.afferent_coupling == 2
    assert metrics.efferent_coupling == 0


def test_efferent_coupling_counts_outgoing_edges() -> None:
    graph = graph_from_edges(("app.cpp", "a.hpp"), ("app.cpp", "b.hpp"))

    metrics = compute_module_metrics(graph, "app.cpp")

    assert metrics.efferent_coupling == 2
    assert metrics.afferent_coupling == 0


def test_edge_direction_determines_which_metric_is_which() -> None:
    """The single assumption everything else rests on.

    An edge runs from dependent to dependency, so out-degree is what a module
    needs and in-degree is what needs it. Reversing the graph inverts every
    instability figure while leaving the numbers superficially plausible.
    """
    graph = graph_from_edges(("dependent.cpp", "dependency.hpp"))

    dependent = compute_module_metrics(graph, "dependent.cpp")
    dependency = compute_module_metrics(graph, "dependency.hpp")

    assert dependent.efferent_coupling == 1
    assert dependent.afferent_coupling == 0
    assert dependent.instability == 1.0

    assert dependency.afferent_coupling == 1
    assert dependency.efferent_coupling == 0
    assert dependency.instability == 0.0


def test_metrics_carry_node_attributes() -> None:
    graph = nx.DiGraph()
    graph.add_node("a.cpp", node_count=250, class_count=3, function_count=12)

    metrics = compute_module_metrics(graph, "a.cpp")

    assert metrics.node_count == 250
    assert metrics.class_count == 3
    assert metrics.function_count == 12


def test_missing_attributes_default_to_zero() -> None:
    """A graph built without AST attributes must still yield coupling figures."""
    graph = nx.DiGraph()
    graph.add_node("a.cpp")

    metrics = compute_module_metrics(graph, "a.cpp")

    assert metrics.node_count == 0
    assert metrics.instability == 0.0


# --- Derived properties ------------------------------------------------------


def test_isolated_module_is_flagged_as_such() -> None:
    """Isolated is not the same fact as stable, and the report says so."""
    graph = nx.DiGraph()
    graph.add_node("orphan.cpp", node_count=0, class_count=0, function_count=0)

    metrics = compute_module_metrics(graph, "orphan.cpp")

    assert metrics.is_isolated
    assert metrics.instability == 0.0
    assert not metrics.is_foundational


def test_foundational_module_is_depended_upon_and_depends_on_nothing() -> None:
    graph = graph_from_edges(("a.cpp", "base.hpp"), ("b.cpp", "base.hpp"))

    metrics = compute_module_metrics(graph, "base.hpp")

    assert metrics.is_foundational
    assert not metrics.is_isolated


def test_module_with_dependencies_is_not_foundational() -> None:
    graph = graph_from_edges(("a.cpp", "mid.hpp"), ("mid.hpp", "base.hpp"))

    assert not compute_module_metrics(graph, "mid.hpp").is_foundational


def test_total_coupling_sums_both_directions() -> None:
    graph = graph_from_edges(
        ("a.cpp", "mid.hpp"), ("b.cpp", "mid.hpp"), ("mid.hpp", "base.hpp")
    )

    assert compute_module_metrics(graph, "mid.hpp").total_coupling == 3


# --- Whole-graph computation -------------------------------------------------


def test_every_node_gets_metrics() -> None:
    graph = graph_from_edges(("a.cpp", "b.hpp"), ("b.hpp", "c.hpp"))

    metrics = compute_all_metrics(graph)

    assert {m.path for m in metrics} == {"a.cpp", "b.hpp", "c.hpp"}


def test_results_are_sorted_by_instability_descending() -> None:
    graph = graph_from_edges(
        ("app.cpp", "mid.hpp"),
        ("app.cpp", "base.hpp"),
        ("mid.hpp", "base.hpp"),
        ("other.cpp", "mid.hpp"),
    )

    metrics = compute_all_metrics(graph)

    instabilities = [m.instability for m in metrics]
    assert instabilities == sorted(instabilities, reverse=True)


def test_sorting_is_deterministic_for_equal_scores() -> None:
    """Ties break on path, so two runs produce identical output.

    Non-deterministic ordering makes report diffs unreadable and would defeat
    tracking metric changes across commits.
    """
    graph = graph_from_edges(("a.cpp", "z.hpp"), ("b.cpp", "z.hpp"))

    first = [m.path for m in compute_all_metrics(graph)]
    second = [m.path for m in compute_all_metrics(graph)]

    assert first == second


def test_empty_graph_yields_no_metrics() -> None:
    assert compute_all_metrics(nx.DiGraph()) == []


# --- A worked example --------------------------------------------------------


def test_hand_calculated_layered_architecture() -> None:
    """A small layered graph with every figure computed by hand.

        app.cpp    -> service.hpp, util.hpp
        service.hpp -> util.hpp
        util.hpp    -> (nothing)

    util.hpp:    Ca=2, Ce=0  ->  I = 0/2   = 0.00
    service.hpp: Ca=1, Ce=1  ->  I = 1/2   = 0.50
    app.cpp:     Ca=0, Ce=2  ->  I = 2/2   = 1.00
    """
    graph = graph_from_edges(
        ("app.cpp", "service.hpp"),
        ("app.cpp", "util.hpp"),
        ("service.hpp", "util.hpp"),
    )

    by_path = {m.path: m for m in compute_all_metrics(graph)}

    assert by_path["util.hpp"] == ModuleMetrics(
        path="util.hpp",
        afferent_coupling=2,
        efferent_coupling=0,
        instability=0.0,
    )
    assert by_path["service.hpp"].instability == pytest.approx(0.5)
    assert by_path["app.cpp"].instability == pytest.approx(1.0)
