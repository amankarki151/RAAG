"""Tests for blast radius computation.

Direction is the thing these tests exist to pin down. An edge runs from
dependent to dependency, so "what breaks if I change X" means walking edges
backwards. Getting that backwards produces a radius that is confidently,
silently wrong — it would name the files X relies on as the files at risk.
"""

from __future__ import annotations

import networkx as nx
import pytest

from raag_master.blast_radius import compute_blast_radius


def graph_from(*edges: tuple[str, str]) -> nx.DiGraph:
    graph = nx.DiGraph()
    graph.add_edges_from(edges)
    return graph


def layered() -> nx.DiGraph:
    """app -> service -> core, plus a second consumer of service.

    core is depended on by everything; app depends on everything.
    """
    return graph_from(
        ("app.cpp", "service.hpp"),
        ("cli.cpp", "service.hpp"),
        ("service.hpp", "core.hpp"),
        ("core.hpp", "util.hpp"),
    )


# --- Direction ---------------------------------------------------------------


def test_dependents_are_files_that_would_break():
    """Walking edges backwards finds what depends on the target."""
    radius = compute_blast_radius(layered(), "service.hpp", depth=2)

    assert set(radius.dependents) == {"app.cpp", "cli.cpp"}


def test_dependencies_are_files_the_target_needs():
    radius = compute_blast_radius(layered(), "service.hpp", depth=2)

    assert set(radius.dependencies) == {"core.hpp", "util.hpp"}


def test_the_two_directions_are_not_confused():
    """The single assertion that catches a reversed traversal."""
    radius = compute_blast_radius(layered(), "core.hpp", depth=3)

    assert "util.hpp" in radius.dependencies
    assert "util.hpp" not in radius.dependents
    assert "service.hpp" in radius.dependents
    assert "service.hpp" not in radius.dependencies


def test_foundational_file_has_many_dependents_and_no_dependencies():
    radius = compute_blast_radius(layered(), "util.hpp", depth=5)

    assert radius.dependencies == []
    assert radius.impact_count == 4


def test_entry_point_has_dependencies_and_no_dependents():
    radius = compute_blast_radius(layered(), "app.cpp", depth=5)

    assert radius.dependents == []
    assert len(radius.dependencies) == 3


# --- Depth -------------------------------------------------------------------


def test_depth_one_finds_only_immediate_neighbours():
    radius = compute_blast_radius(layered(), "core.hpp", depth=1)

    assert radius.dependents == ["service.hpp"]
    assert radius.dependencies == ["util.hpp"]


def test_greater_depth_reaches_further():
    shallow = compute_blast_radius(layered(), "core.hpp", depth=1)
    deep = compute_blast_radius(layered(), "core.hpp", depth=3)

    assert len(deep.dependents) > len(shallow.dependents)


def test_results_are_ordered_nearest_first():
    """Breadth-first ordering means truncation keeps the most relevant files.

    A file one hop away matters more than one five hops away, and a cap that
    kept an arbitrary slice would discard exactly the wrong ones.
    """
    radius = compute_blast_radius(layered(), "util.hpp", depth=5)

    assert radius.dependents[0] == "core.hpp"
    assert radius.dependents.index("core.hpp") < radius.dependents.index("app.cpp")


def test_depth_below_one_is_rejected():
    with pytest.raises(ValueError, match="at least 1"):
        compute_blast_radius(layered(), "core.hpp", depth=0)


# --- Cycles and self-reference -----------------------------------------------


def test_cycles_do_not_cause_infinite_traversal():
    graph = graph_from(("a.hpp", "b.hpp"), ("b.hpp", "c.hpp"), ("c.hpp", "a.hpp"))

    radius = compute_blast_radius(graph, "a.hpp", depth=10)

    assert set(radius.dependents) == {"b.hpp", "c.hpp"}


def test_target_never_appears_in_its_own_radius():
    graph = graph_from(("a.hpp", "b.hpp"), ("b.hpp", "a.hpp"))

    radius = compute_blast_radius(graph, "a.hpp", depth=5)

    assert "a.hpp" not in radius.dependents
    assert "a.hpp" not in radius.dependencies


# --- Truncation --------------------------------------------------------------


def test_large_radius_is_truncated_and_says_so():
    """A truncated radius is a partial answer, and a caller that thinks it has
    the whole picture will draw conclusions the data doesn't support."""
    graph = nx.DiGraph()
    for i in range(100):
        graph.add_edge(f"dep{i}.cpp", "hub.hpp")

    radius = compute_blast_radius(graph, "hub.hpp", depth=1, max_dependents=10)

    assert len(radius.dependents) == 10
    assert radius.truncated
    assert radius.total_reachable == 100
    assert "truncated" in radius.summary_line()


def test_small_radius_is_not_marked_truncated():
    radius = compute_blast_radius(layered(), "service.hpp", depth=2)

    assert not radius.truncated


# --- all_paths ---------------------------------------------------------------


def test_all_paths_leads_with_the_target():
    """A refactor request is about the target; its own code should rank first."""
    radius = compute_blast_radius(layered(), "service.hpp", depth=2)

    assert radius.all_paths[0] == "service.hpp"


def test_all_paths_deduplicates():
    """A file can be both upstream and downstream in a cyclic graph."""
    graph = graph_from(("a.hpp", "b.hpp"), ("b.hpp", "a.hpp"))

    paths = compute_blast_radius(graph, "a.hpp", depth=3).all_paths

    assert len(paths) == len(set(paths))


def test_all_paths_covers_both_directions():
    radius = compute_blast_radius(layered(), "service.hpp", depth=2)

    assert set(radius.all_paths) == {
        "service.hpp",
        "app.cpp",
        "cli.cpp",
        "core.hpp",
        "util.hpp",
    }


# --- Errors ------------------------------------------------------------------


def test_missing_target_raises_rather_than_returning_empty():
    """An empty radius means 'nothing is affected'. A missing file means 'the
    question was wrong'. Conflating them lets a typo look like a safe change.
    """
    with pytest.raises(KeyError, match="not in the dependency graph"):
        compute_blast_radius(layered(), "does/not/exist.hpp")


def test_isolated_file_has_an_empty_radius():
    graph = layered()
    graph.add_node("orphan.cpp")

    radius = compute_blast_radius(graph, "orphan.cpp", depth=3)

    assert radius.dependents == []
    assert radius.dependencies == []
    assert radius.all_paths == ["orphan.cpp"]
