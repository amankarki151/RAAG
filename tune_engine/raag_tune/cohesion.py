"""Cohesion analysis: Lack of Cohesion of Methods.

Coupling describes relationships *between* files. Cohesion describes whether a
single class holds together — whether its methods are working on the same state
or on unrelated concerns that happen to share a declaration.

Two variants are computed:

**LCOM1** (Chidamber & Kemerer) counts method pairs sharing no field against
pairs that do. It is the original definition and is widely cited, but it
overstates on classes with accessors: a getter touching one field looks
unrelated to every method that does not touch that field.

**LCOM4** (Hitz & Montazeri) counts connected components in a graph where
methods are nodes, joined when they share a field or when one calls the other.
An LCOM4 of 1 means the class is cohesive. A value of *n* means it splits
cleanly into *n* independent groups — which is directly actionable, because the
components name the classes it should have been.

LCOM4 is the primary figure. LCOM1 is reported alongside it because the two
disagreeing is itself informative: a high LCOM1 with an LCOM4 of 1 usually means
accessors, not a design problem.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import networkx as nx

from raag_tune.ast_types import AstArena, AstNodeKind, SnapshotEntry

__all__ = ["ClassCohesion", "compute_cohesion", "compute_file_cohesion"]


@dataclass(frozen=True, slots=True)
class ClassCohesion:
    """Cohesion figures for one class.

    Attributes:
        path: File the class is declared in.
        class_name: The class's own name.
        method_count: Methods found on the class.
        field_count: Distinct fields any method accesses.
        lcom1: Chidamber & Kemerer LCOM. Zero is cohesive; higher is worse.
        lcom4: Hitz & Montazeri LCOM — connected components among methods.
            One is cohesive; *n* means the class splits into *n* groups.
        components: The method groupings LCOM4 found, largest first. These are
            the concrete split suggestion, not just a score.
    """

    path: str
    class_name: str
    method_count: int
    field_count: int
    lcom1: int
    lcom4: int
    components: list[list[str]] = field(default_factory=list)

    @property
    def is_cohesive(self) -> bool:
        """Whether the class holds together as one responsibility.

        A class with fewer than two methods is trivially cohesive: there is no
        pair of methods that could fail to relate. Reporting such classes as
        violations would bury the real ones in noise.
        """
        return self.lcom4 <= 1

    @property
    def suggested_split(self) -> int:
        """How many classes this one could become. One means leave it alone."""
        return max(1, self.lcom4)


def _method_nodes(arena: AstArena, class_index: int) -> list[int]:
    """Indices of methods declared directly inside a class.

    Descends through the class body rather than taking direct children, because
    grammars wrap members in a body node. Nested classes are not descended into:
    their methods belong to the nested class, and attributing them to the outer
    one would merge two classes' cohesion into a single misleading figure.
    """
    methods: list[int] = []
    stack = list(arena.child_indices(class_index))

    while stack:
        index = stack.pop()
        node = arena[index]

        if node.kind is AstNodeKind.CLASS:
            continue

        if node.kind is AstNodeKind.FUNCTION:
            methods.append(index)
            continue

        stack.extend(arena.child_indices(index))

    return methods


def _accessed_fields(arena: AstArena, method_index: int) -> set[str]:
    """Field names a method touches, including through nested scopes.

    A field accessed inside a loop or a lambda is still a field the method
    depends on, so the whole subtree is walked rather than the immediate
    children.
    """
    fields: set[str] = set()

    for node, _ in arena.walk(start=method_index):
        if node.kind is AstNodeKind.FIELD_ACCESS and node.name:
            fields.add(node.name)

    return fields


def _called_names(arena: AstArena, method_index: int) -> set[str]:
    """Names this method calls, reduced to their final component.

    ``self.helper()`` arrives as ``self.helper``; the class-local name is what
    matters for linking two methods together.
    """
    called: set[str] = set()

    for node, _ in arena.walk(start=method_index):
        if node.kind is AstNodeKind.CALL_EXPRESSION and node.name:
            called.add(node.name.rsplit(".", 1)[-1].rsplit("::", 1)[-1])

    return called


def _lcom1(field_sets: list[set[str]]) -> int:
    """Chidamber & Kemerer LCOM: disjoint pairs minus sharing pairs, floored.

    Floored at zero because the original definition can go negative when most
    pairs share state, and a negative "lack of cohesion" is not meaningful —
    it just means very cohesive, which zero already says.
    """
    disjoint = 0
    sharing = 0

    for i in range(len(field_sets)):
        for j in range(i + 1, len(field_sets)):
            if field_sets[i] & field_sets[j]:
                sharing += 1
            else:
                disjoint += 1

    return max(0, disjoint - sharing)


def _lcom4(
    method_names: list[str],
    field_sets: list[set[str]],
    call_sets: list[set[str]],
) -> tuple[int, list[list[str]]]:
    """Hitz & Montazeri LCOM: connected components among methods.

    Two methods are joined when they share a field, or when one calls the
    other. The call edge matters: a method that delegates entirely to another
    touches no fields itself, and without call edges it would appear as its own
    isolated component in every class that uses delegation.
    """
    graph = nx.Graph()
    graph.add_nodes_from(range(len(method_names)))

    name_to_index = {name: index for index, name in enumerate(method_names)}

    for i in range(len(method_names)):
        for j in range(i + 1, len(method_names)):
            if field_sets[i] & field_sets[j]:
                graph.add_edge(i, j)

        for called in call_sets[i]:
            target = name_to_index.get(called)
            if target is not None and target != i:
                graph.add_edge(i, target)

    components = [
        sorted(method_names[index] for index in component)
        for component in nx.connected_components(graph)
    ]
    components.sort(key=len, reverse=True)

    return len(components), components


def compute_file_cohesion(entry: SnapshotEntry) -> list[ClassCohesion]:
    """Compute cohesion for every class declared in one file."""
    arena = entry.arena
    results: list[ClassCohesion] = []

    for index, node in enumerate(arena.nodes):
        if node.kind is not AstNodeKind.CLASS or not node.name:
            continue

        # The Sample Engine encodes inheritance as "Derived : Base"; only the
        # class's own name is wanted here.
        class_name = node.name.partition(" : ")[0].strip()
        if not class_name:
            continue

        method_indices = _method_nodes(arena, index)
        method_names = [arena[i].name or f"<anonymous:{i}>" for i in method_indices]

        field_sets = [_accessed_fields(arena, i) for i in method_indices]
        call_sets = [_called_names(arena, i) for i in method_indices]

        distinct_fields = set().union(*field_sets) if field_sets else set()

        if len(method_indices) < 2:
            # Trivially cohesive. Computing LCOM over fewer than two methods
            # produces a number with no pairs behind it.
            results.append(
                ClassCohesion(
                    path=entry.path,
                    class_name=class_name,
                    method_count=len(method_indices),
                    field_count=len(distinct_fields),
                    lcom1=0,
                    lcom4=1 if method_indices else 0,
                    components=[method_names] if method_names else [],
                )
            )
            continue

        lcom4, components = _lcom4(method_names, field_sets, call_sets)

        results.append(
            ClassCohesion(
                path=entry.path,
                class_name=class_name,
                method_count=len(method_indices),
                field_count=len(distinct_fields),
                lcom1=_lcom1(field_sets),
                lcom4=lcom4,
                components=components,
            )
        )

    return results


def compute_cohesion(entries: list[SnapshotEntry]) -> list[ClassCohesion]:
    """Compute cohesion for every class in the snapshot.

    Sorted worst first — highest LCOM4, then most methods. A large incohesive
    class is a more urgent finding than a small one, since the cost of splitting
    grows with size while the benefit is already established by the score.
    """
    results: list[ClassCohesion] = []
    for entry in entries:
        results.extend(compute_file_cohesion(entry))

    results.sort(key=lambda c: (-c.lcom4, -c.method_count, c.path, c.class_name))
    return results
