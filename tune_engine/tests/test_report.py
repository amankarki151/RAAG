"""Tests for report assembly and threshold evaluation.
The report is where numbers become judgements, so these tests focus on the
policy boundaries: what fires, what does not, and what a violation says when it
does. A violation message that gives no measured value and no threshold is
unactionable, and there are tests asserting both are present.
"""

from __future__ import annotations

import json
from pathlib import Path

import networkx as nx
import pytest

from raag_tune.ast_types import AstArena, AstNode, AstNodeKind, SnapshotEntry
from raag_tune.report import Severity, Thresholds, build_report


def graph_from_edges(*edges: tuple[str, str]) -> nx.DiGraph:
    graph = nx.DiGraph()
    for source, target in edges:
        for node in (source, target):
            if node not in graph:
                graph.add_node(node, node_count=0, class_count=0, function_count=0)
        graph.add_edge(source, target)
    return graph


def empty_entry(path: str) -> SnapshotEntry:
    return SnapshotEntry(
        path=path,
        arena=AstArena(
            nodes=[
                AstNode(
                    kind=AstNodeKind.FILE,
                    name="",
                    byte_start=0,
                    byte_end=1,
                    first_child_index=0,
                    child_count=0,
                    parent_index=-1,
                )
            ]
        ),
    )


def hub_graph(dependents: int, dependencies: int) -> nx.DiGraph:
    """A hub with the given in- and out-degree, for instability tests."""
    edges: list[tuple[str, str]] = []
    edges.extend((f"dep{i}.cpp", "hub.hpp") for i in range(dependents))
    edges.extend(("hub.hpp", f"lib{i}.hpp") for i in range(dependencies))
    return graph_from_edges(*edges)


# --- Clean repositories ------------------------------------------------------


def test_layered_graph_produces_no_errors() -> None:
    graph = graph_from_edges(("app.cpp", "svc.hpp"), ("svc.hpp", "util.hpp"))

    report = build_report(graph, [])

    assert report.error_count == 0
    assert report.passed


def test_empty_graph_does_not_raise() -> None:
    report = build_report(nx.DiGraph(), [])

    assert report.passed
    assert report.modules == []
    assert report.violations == []


# --- Instability -------------------------------------------------------------


def test_unstable_hub_is_an_error() -> None:
    """Ca=4, Ce=6 gives I=0.6, above the 0.4 default, with enough dependents."""
    report = build_report(hub_graph(dependents=4, dependencies=6), [])

    instability = [v for v in report.violations if v.rule == "instability"]

    assert len(instability) == 1
    assert instability[0].subject == "hub.hpp"
    assert instability[0].severity is Severity.ERROR
    assert not report.passed


def test_unstable_leaf_is_not_flagged() -> None:
    """An unstable module nothing depends on is uninteresting.

    Nothing breaks when it changes, which is why the check is gated on afferent
    coupling rather than firing on the ratio alone. An adapter or CLI entry
    point should be unstable — that is its job.
    """
    graph = graph_from_edges(
        ("cli.cpp", "a.hpp"), ("cli.cpp", "b.hpp"), ("cli.cpp", "c.hpp")
    )

    report = build_report(graph, [])

    assert not any(v.rule == "instability" for v in report.violations)
    assert report.passed


def test_afferent_gate_is_respected() -> None:
    """Two dependents is below the default gate of three."""
    report = build_report(hub_graph(dependents=2, dependencies=8), [])

    assert not any(v.rule == "instability" for v in report.violations)


def test_stable_hub_is_not_flagged() -> None:
    report = build_report(hub_graph(dependents=8, dependencies=1), [])

    assert not any(v.rule == "instability" for v in report.violations)


def test_instability_threshold_is_configurable() -> None:
    graph = hub_graph(dependents=4, dependencies=6)

    strict = build_report(graph, [], thresholds=Thresholds(max_instability=0.1))
    lenient = build_report(graph, [], thresholds=Thresholds(max_instability=0.9))

    assert any(v.rule == "instability" for v in strict.violations)
    assert not any(v.rule == "instability" for v in lenient.violations)


def test_violation_carries_measurement_and_threshold() -> None:
    """A violation that only says "too high" cannot be acted on."""
    report = build_report(hub_graph(dependents=4, dependencies=6), [])

    violation = next(v for v in report.violations if v.rule == "instability")

    assert violation.measured == pytest.approx(0.6)
    assert violation.threshold == pytest.approx(0.4)
    assert "hub.hpp" in violation.format_line()


# --- Efferent coupling -------------------------------------------------------


def test_high_efferent_coupling_is_a_warning_not_an_error() -> None:
    """A composition root legitimately wires many things together.

    Failing a build on it would train people to disable the gate.
    """
    graph = graph_from_edges(*[("app.cpp", f"m{i}.hpp") for i in range(25)])

    report = build_report(graph, [])

    efferent = [v for v in report.violations if v.rule == "efferent-coupling"]

    assert len(efferent) == 1
    assert efferent[0].severity is Severity.WARNING
    assert report.passed


def test_efferent_threshold_is_configurable() -> None:
    graph = graph_from_edges(*[("app.cpp", f"m{i}.hpp") for i in range(5)])

    report = build_report(graph, [], thresholds=Thresholds(max_efferent_coupling=3))

    assert any(v.rule == "efferent-coupling" for v in report.violations)


# --- Cycles ------------------------------------------------------------------


def test_cycle_is_an_error_by_default() -> None:
    graph = graph_from_edges(("a.hpp", "b.hpp"), ("b.hpp", "a.hpp"))

    report = build_report(graph, [])

    cycles = [v for v in report.violations if v.rule == "dependency-cycle"]

    assert len(cycles) == 1
    assert cycles[0].severity is Severity.ERROR
    assert not report.passed


def test_cycle_severity_is_configurable() -> None:
    graph = graph_from_edges(("a.hpp", "b.hpp"), ("b.hpp", "a.hpp"))

    report = build_report(graph, [], thresholds=Thresholds(fail_on_cycles=False))

    assert report.passed
    assert report.warning_count == 1


def test_cycle_members_are_recorded() -> None:
    graph = graph_from_edges(("a.hpp", "b.hpp"), ("b.hpp", "c.hpp"), ("c.hpp", "a.hpp"))

    report = build_report(graph, [])

    assert len(report.cycles) == 1
    assert sorted(report.cycles[0]) == ["a.hpp", "b.hpp", "c.hpp"]


# --- Ordering and summaries --------------------------------------------------


def test_errors_sort_before_warnings() -> None:
    graph = graph_from_edges(
        ("a.hpp", "b.hpp"),
        ("b.hpp", "a.hpp"),
        *[("app.cpp", f"m{i}.hpp") for i in range(25)],
    )

    report = build_report(graph, [])

    severities = [v.severity for v in report.violations]
    first_warning = severities.index(Severity.WARNING)

    assert all(s is Severity.ERROR for s in severities[:first_warning])


def test_most_unstable_excludes_modules_nothing_depends_on() -> None:
    graph = graph_from_edges(
        ("leaf.cpp", "a.hpp"), ("x.cpp", "hub.hpp"), ("hub.hpp", "b.hpp")
    )

    report = build_report(graph, [])

    assert all(m.afferent_coupling > 0 for m in report.most_unstable())


def test_most_depended_upon_is_sorted_descending() -> None:
    graph = graph_from_edges(
        ("a.cpp", "core.hpp"), ("b.cpp", "core.hpp"), ("c.cpp", "minor.hpp")
    )

    report = build_report(graph, [])
    top = report.most_depended_upon(2)

    assert top[0].path == "core.hpp"
    assert top[0].afferent_coupling >= top[1].afferent_coupling


def test_report_records_resolution_summary() -> None:
    from raag_tune.edge_extractor import extract_dependencies

    entries = [empty_entry("a.cpp")]
    extraction = extract_dependencies(entries)

    report = build_report(
        graph_from_edges(("a.cpp", "b.hpp")), entries, extraction=extraction
    )

    assert "imports" in report.resolution_summary


# --- Serialization -----------------------------------------------------------


def test_report_serializes_to_json(tmp_path: Path) -> None:
    graph = graph_from_edges(("a.cpp", "b.hpp"))
    report = build_report(graph, [])

    destination = tmp_path / "nested" / "metrics.json"
    report.write_json(destination)

    payload = json.loads(destination.read_text())

    assert "summary" in payload
    assert "modules" in payload
    assert "thresholds" in payload
    assert len(payload["modules"]) == 2


def test_json_output_is_stable_across_runs(tmp_path: Path) -> None:
    """Unstable output makes report diffs unreadable and defeats tracking
    metric changes across commits."""
    graph = graph_from_edges(("a.cpp", "b.hpp"), ("c.cpp", "b.hpp"))

    first = tmp_path / "one.json"
    second = tmp_path / "two.json"
    build_report(graph, []).write_json(first)
    build_report(graph, []).write_json(second)

    assert first.read_text() == second.read_text()
