"""Computing which files a change can actually reach.

This is the module that makes RAAG's retrieval structural rather than
lexical. Given a file somebody wants to change, it answers: what else in this
repository is implicated?

The answer has two halves, and conflating them produces bad context.

**Dependents** are files that would break. An edge runs from dependent to
dependency, so anything with a path *to* the target transitively depends on it.
Change the target's interface and these are the call sites that stop compiling.

**Dependencies** are files needed to understand the target, not files at risk.
The target calls into them; they have no idea it exists. They belong in context
because a reviewer cannot judge a change without seeing what it relies on, but
they are not part of the impact.

Both are bounded by depth. Transitive dependency closure in a real repository
is most of the repository — a foundational header reaches everything — and
"everything is affected" is not a useful answer to "what does this affect".
"""

from __future__ import annotations

from dataclasses import dataclass, field

import networkx as nx

__all__ = ["BlastRadius", "compute_blast_radius"]


@dataclass(frozen=True, slots=True)
class BlastRadius:
    """The set of files implicated by a change to one target.

    Attributes:
        target: The file being changed.
        dependents: Files that transitively depend on the target, nearest
            first. These are what break.
        dependencies: Files the target transitively depends on, nearest first.
            These are context, not impact.
        depth: Traversal depth used.
        truncated: Whether a limit stopped the traversal before it exhausted
            the reachable set. Reported because a truncated radius is a
            partial answer, and a caller that thinks it has the whole picture
            will draw conclusions the data does not support.
        total_reachable: How many files were reachable before truncation.
    """

    target: str
    dependents: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    depth: int = 2
    truncated: bool = False
    total_reachable: int = 0

    @property
    def all_paths(self) -> list[str]:
        """Every file in the radius, target first.

        This is what gets handed to the vector store as a retrieval filter.
        The target leads because a refactor request is about the target, and
        its own code should rank before anything merely related to it.
        """
        seen: set[str] = set()
        ordered: list[str] = []

        for path in [self.target, *self.dependents, *self.dependencies]:
            if path not in seen:
                seen.add(path)
                ordered.append(path)

        return ordered

    @property
    def impact_count(self) -> int:
        """Files that would actually break. The number worth quoting."""
        return len(self.dependents)

    def summary_line(self) -> str:
        parts = [
            f"{self.impact_count} dependent(s)",
            f"{len(self.dependencies)} dependency(ies)",
            f"depth {self.depth}",
        ]
        if self.truncated:
            parts.append(f"truncated from {self.total_reachable}")
        return ", ".join(parts)


def _bounded_traversal(
    graph: nx.DiGraph,
    start: str,
    depth: int,
    *,
    reverse: bool,
) -> list[str]:
    """Breadth-first walk from ``start``, up to ``depth`` hops.

    Breadth-first rather than depth-first so results come back ordered by
    distance. A file one hop from the target is more likely to matter than one
    five hops away, and when a limit truncates the list, that ordering means
    the nearest — most relevant — files survive rather than an arbitrary slice.

    Args:
        reverse: Follow edges backwards, finding what depends on ``start``
            rather than what ``start`` depends on.
    """
    neighbours = graph.predecessors if reverse else graph.successors

    visited = {start}
    frontier = [start]
    ordered: list[str] = []

    for _ in range(depth):
        next_frontier: list[str] = []

        for node in frontier:
            for neighbour in neighbours(node):
                if neighbour in visited:
                    continue
                visited.add(neighbour)
                ordered.append(neighbour)
                next_frontier.append(neighbour)

        if not next_frontier:
            break
        frontier = next_frontier

    return ordered


def compute_blast_radius(
    graph: nx.DiGraph,
    target: str,
    *,
    depth: int = 2,
    max_dependents: int = 40,
    max_dependencies: int = 20,
) -> BlastRadius:
    """Compute what a change to ``target`` reaches.

    Args:
        graph: The dependency graph. Edges run dependent to dependency.
        target: File being changed. Must exist in the graph.
        depth: Hops to traverse in each direction.
        max_dependents: Cap on files-that-break. Higher than the dependency
            cap because impact is the question being asked; dependencies are
            supporting context.
        max_dependencies: Cap on files-the-target-needs.

    Returns:
        The radius, with truncation reported rather than hidden.

    Raises:
        KeyError: ``target`` is not in the graph. Raised rather than returning
            an empty radius, because an empty radius means "nothing is
            affected" and a missing file means "the question was wrong" —
            silently conflating them would let a typo look like a safe change.
    """
    if target not in graph:
        raise KeyError(
            f"{target!r} is not in the dependency graph. "
            f"Check the path matches one in the snapshot exactly."
        )

    if depth < 1:
        raise ValueError(f"depth must be at least 1, got {depth}")

    dependents = _bounded_traversal(graph, target, depth, reverse=True)
    dependencies = _bounded_traversal(graph, target, depth, reverse=False)

    total = len(dependents) + len(dependencies)
    truncated = len(dependents) > max_dependents or len(dependencies) > max_dependencies

    return BlastRadius(
        target=target,
        dependents=dependents[:max_dependents],
        dependencies=dependencies[:max_dependencies],
        depth=depth,
        truncated=truncated,
        total_reachable=total,
    )
