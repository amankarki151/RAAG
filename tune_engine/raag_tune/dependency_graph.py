"""Construction and analysis of the repository dependency graph.

The graph is a ``networkx.DiGraph``: nodes are source files, edges are
dependencies between them. Parallel edges are collapsed into a single edge with
a weight, because two files importing each other twice are not more coupled
than files importing each other once — the relationship exists or it does not.

The graph is **not** assumed to be acyclic. Real repositories contain cycles,
particularly C++ header sets, and a tool that assumes otherwise either crashes
or silently drops edges on exactly the codebases that most need analysing.
Cycle detection is therefore a feature rather than an error path, and layering
runs over the condensation.
"""

from __future__ import annotations

from dataclasses import dataclass

import networkx as nx

from raag_tune.ast_types import AstNodeKind, SnapshotEntry
from raag_tune.edge_extractor import ExtractionReport, extract_dependencies
from raag_tune.graph_types import EdgeKind

__all__ = [
    "GraphSummary",
    "build_dependency_graph",
    "find_cycles",
    "summarise_graph",
    "topological_layers",
]


@dataclass(frozen=True, slots=True)
class GraphSummary:
    """Headline figures describing a dependency graph."""

    node_count: int
    edge_count: int
    import_edges: int
    inheritance_edges: int
    cycle_count: int
    largest_cycle: int
    isolated_files: int
    layer_count: int
    density: float

    def report_lines(self) -> list[str]:
        return [
            f"Files              {self.node_count}",
            f"Dependencies       {self.edge_count} "
            f"({self.import_edges} import, {self.inheritance_edges} inheritance)",
            f"Density            {self.density:.4f}",
            f"Isolated files     {self.isolated_files}",
            f"Dependency layers  {self.layer_count}",
            f"Cycles             {self.cycle_count}"
            + (f" (largest: {self.largest_cycle} files)" if self.cycle_count else ""),
        ]


def build_dependency_graph(
    entries: list[SnapshotEntry],
    *,
    report: ExtractionReport | None = None,
) -> tuple[nx.DiGraph, ExtractionReport]:
    """Build the dependency graph for a parsed repository.

    Every parsed file becomes a node, including files with no dependencies in
    either direction. Dropping them would flatter the graph: a file nothing
    depends on is a real and interesting fact about a codebase, not an absence
    of data.

    Args:
        entries: Parsed files from a snapshot.
        report: A pre-computed extraction report. Passing one avoids repeating
            the resolution work when the caller has already run it.

    Returns:
        The graph, and the extraction report describing how its edges resolved.
    """
    if report is None:
        report = extract_dependencies(entries)

    graph = nx.DiGraph()

    for entry in entries:
        counts = entry.arena.count_by_kind()
        graph.add_node(
            entry.path,
            node_count=len(entry.arena),
            class_count=counts[AstNodeKind.CLASS],
            function_count=counts[AstNodeKind.FUNCTION],
            import_count=counts[AstNodeKind.IMPORT],
        )

    for dependency in report.dependencies:
        # Both endpoints are guaranteed present: extraction only emits edges
        # whose target resolved to a parsed file.
        if graph.has_edge(dependency.source, dependency.target):
            edge = graph.edges[dependency.source, dependency.target]
            edge["weight"] += 1
            edge["kinds"].add(dependency.kind)
        else:
            graph.add_edge(
                dependency.source,
                dependency.target,
                weight=1,
                kinds={dependency.kind},
            )

    return graph, report


def find_cycles(graph: nx.DiGraph, *, limit: int | None = None) -> list[list[str]]:
    """Find dependency cycles, longest first.

    Uses strongly connected components rather than enumerating simple cycles.
    Enumeration is exponential in the worst case and a densely tangled header
    set is precisely the worst case — the analysis would hang on the codebases
    it is most needed for. Each component of more than one node is a set of
    files that mutually depend, which is the actionable unit anyway.

    Args:
        graph: The dependency graph.
        limit: Return at most this many components.

    Returns:
        Lists of file paths, each list a mutually dependent group.
    """
    components = [
        sorted(component)
        for component in nx.strongly_connected_components(graph)
        if len(component) > 1
    ]

    # A self-loop is a single-node component that still represents a cycle,
    # though extraction filters these out before they reach the graph.
    components.extend([node] for node in graph.nodes if graph.has_edge(node, node))

    components.sort(key=len, reverse=True)
    return components[:limit] if limit is not None else components


def topological_layers(graph: nx.DiGraph) -> list[list[str]]:
    """Group files into dependency layers, foundations first.

    Layer 0 depends on nothing else in the repository; layer 1 depends only on
    layer 0, and so on. This is the shape an architecture diagram wants.

    The graph is reversed before sorting. An edge runs from dependent to
    dependency, so a foundation has no outgoing edges — but topological
    generations yield nodes with no *incoming* edges first. Reversing puts
    foundations at generation zero, which is the ordering callers expect.

    Cycles are handled by condensing each strongly connected component to a
    single vertex. The condensation is always acyclic, so a tangled repository
    still produces a usable layering, with the tangle appearing as one wide
    layer rather than as a failure.
    """
    condensation = nx.condensation(graph.reverse(copy=True))
    members: dict[int, list[str]] = {
        component: sorted(condensation.nodes[component]["members"])
        for component in condensation.nodes
    }

    layers: list[list[str]] = []
    for generation in nx.topological_generations(condensation):
        files: list[str] = []
        for component in generation:
            files.extend(members[component])
        layers.append(sorted(files))

    return layers


def summarise_graph(graph: nx.DiGraph) -> GraphSummary:
    """Compute headline figures for a dependency graph."""
    import_edges = sum(
        1 for _, _, data in graph.edges(data=True) if EdgeKind.IMPORT in data["kinds"]
    )
    inheritance_edges = sum(
        1
        for _, _, data in graph.edges(data=True)
        if EdgeKind.INHERITANCE in data["kinds"]
    )

    cycles = find_cycles(graph)
    isolated = sum(
        1
        for node in graph.nodes
        if graph.in_degree(node) == 0 and graph.out_degree(node) == 0
    )

    return GraphSummary(
        node_count=graph.number_of_nodes(),
        edge_count=graph.number_of_edges(),
        import_edges=import_edges,
        inheritance_edges=inheritance_edges,
        cycle_count=len(cycles),
        largest_cycle=len(cycles[0]) if cycles else 0,
        isolated_files=isolated,
        layer_count=len(topological_layers(graph)),
        density=nx.density(graph),
    )
