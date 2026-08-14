"""Coupling and stability metrics over the dependency graph.

These are Robert Martin's package metrics, applied at file granularity. They are
pure functions of the graph: no I/O, no global state, nothing but degree counts
and arithmetic. That makes them trivially testable against hand-calculated
examples, which matters because a metric that is subtly wrong produces
confident, plausible, incorrect architectural advice.
"""

from __future__ import annotations

from dataclasses import dataclass

import networkx as nx

__all__ = [
    "ModuleMetrics",
    "compute_all_metrics",
    "compute_module_metrics",
    "instability",
]


def instability(afferent: int, efferent: int) -> float:
    """Martin's instability index, ``I = Ce / (Ca + Ce)``.

    Ranges from 0.0 to 1.0.

    * **0.0** — maximally stable. Nothing this module depends on can force it to
      change, but many things depend on it, so changing it is expensive.
    * **1.0** — maximally unstable. Nothing depends on it, so it is cheap to
      change, but it is at the mercy of everything it depends on.

    A module with no dependencies in either direction has no meaningful
    instability. Returning 0.0 for that case is a convention, not a
    measurement: an isolated file is not *stable* in Martin's sense, it is
    simply unconnected. Callers distinguish the two through ``is_isolated``
    rather than by reading a number that cannot mean anything.
    """
    total = afferent + efferent
    if total == 0:
        return 0.0
    return efferent / total


@dataclass(frozen=True, slots=True)
class ModuleMetrics:
    """Coupling figures for one file.

    Attributes:
        path: Repository-relative path.
        afferent_coupling: Ca. How many files depend on this one. High Ca means
            this module carries responsibility — changing it ripples outward.
        efferent_coupling: Ce. How many files this one depends on. High Ce means
            fragility: upstream changes propagate here.
        instability: I = Ce / (Ca + Ce).
        node_count: AST nodes in the file, a rough size proxy.
        class_count: Class declarations in the file.
        function_count: Function definitions in the file.
    """

    path: str
    afferent_coupling: int
    efferent_coupling: int
    instability: float
    node_count: int = 0
    class_count: int = 0
    function_count: int = 0

    @property
    def is_isolated(self) -> bool:
        """No dependencies in either direction.

        Distinguished from stable because the instability figure for such a
        module is a convention rather than a measurement.
        """
        return self.afferent_coupling == 0 and self.efferent_coupling == 0

    @property
    def total_coupling(self) -> int:
        return self.afferent_coupling + self.efferent_coupling

    @property
    def is_foundational(self) -> bool:
        """Depended upon, depending on nothing.

        The base of the dependency hierarchy. These modules should change
        rarely; when they do, the blast radius is the whole graph beneath them.
        """
        return self.afferent_coupling > 0 and self.efferent_coupling == 0


def compute_module_metrics(graph: nx.DiGraph, path: str) -> ModuleMetrics:
    """Compute coupling metrics for a single file.

    Edge direction is what makes this correct. An edge runs from dependent to
    dependency, so out-degree counts what a module *needs* (efferent) and
    in-degree counts what needs *it* (afferent). Reversing the graph would
    invert every instability figure while leaving the numbers superficially
    plausible.
    """
    afferent = graph.in_degree(path)
    efferent = graph.out_degree(path)

    attributes = graph.nodes[path]

    return ModuleMetrics(
        path=path,
        afferent_coupling=int(afferent),
        efferent_coupling=int(efferent),
        instability=instability(int(afferent), int(efferent)),
        node_count=attributes.get("node_count", 0),
        class_count=attributes.get("class_count", 0),
        function_count=attributes.get("function_count", 0),
    )


def compute_all_metrics(graph: nx.DiGraph) -> list[ModuleMetrics]:
    """Compute coupling metrics for every file in the graph.

    Returned sorted by instability descending, then by afferent coupling
    descending. That ordering puts the most interesting modules first: an
    unstable module that many others depend on is the clearest architectural
    problem a codebase can have.
    """
    metrics = [compute_module_metrics(graph, path) for path in graph.nodes]
    metrics.sort(key=lambda m: (-m.instability, -m.afferent_coupling, m.path))
    return metrics
