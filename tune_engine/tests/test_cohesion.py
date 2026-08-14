"""Tests for LCOM cohesion analysis.

Arenas are built by hand so each class's method/field structure is visible next
to the expected score. LCOM values are hand-calculated from the definitions in
docs/METRICS.md, not copied from a run.
"""

from __future__ import annotations

from raag_tune.ast_types import AstArena, AstNode, AstNodeKind, SnapshotEntry
from raag_tune.cohesion import compute_cohesion, compute_file_cohesion


class ArenaBuilder:
    """Builds an arena while maintaining the contiguity invariant.

    Children of a node must occupy a contiguous index range, so each node's
    children are appended in one batch and the parent's range backfilled — the
    same discipline the C++ builder follows.
    """

    def __init__(self) -> None:
        self.nodes: list[AstNode] = []

    def add(self, kind: AstNodeKind, name: str = "", parent: int = -1) -> int:
        index = len(self.nodes)
        self.nodes.append(
            AstNode(
                kind=kind,
                name=name,
                byte_start=index,
                byte_end=index + 1,
                first_child_index=0,
                child_count=0,
                parent_index=parent,
            )
        )
        return index

    def attach(self, parent: int, children: list[int]) -> None:
        if not children:
            return
        node = self.nodes[parent]
        self.nodes[parent] = AstNode(
            kind=node.kind,
            name=node.name,
            byte_start=node.byte_start,
            byte_end=node.byte_end,
            first_child_index=min(children),
            child_count=len(children),
            parent_index=node.parent_index,
        )

    def build(self, path: str) -> SnapshotEntry:
        return SnapshotEntry(path=path, arena=AstArena(nodes=self.nodes))


def build_class(
    path: str,
    class_name: str,
    methods: dict[str, tuple[list[str], list[str]]],
) -> SnapshotEntry:
    """Build a file containing one class.

    Args:
        methods: Method name to (accessed fields, called method names).
    """
    builder = ArenaBuilder()
    root = builder.add(AstNodeKind.FILE)
    class_index = builder.add(AstNodeKind.CLASS, class_name, parent=root)
    builder.attach(root, [class_index])

    method_indices: list[int] = []
    for method_name in methods:
        method_indices.append(
            builder.add(AstNodeKind.FUNCTION, method_name, parent=class_index)
        )
    builder.attach(class_index, method_indices)

    for method_index, (fields, calls) in zip(
        method_indices, methods.values(), strict=True
    ):
        children: list[int] = []
        for field_name in fields:
            children.append(
                builder.add(AstNodeKind.FIELD_ACCESS, field_name, parent=method_index)
            )
        for call_name in calls:
            children.append(
                builder.add(
                    AstNodeKind.CALL_EXPRESSION,
                    f"self.{call_name}",
                    parent=method_index,
                )
            )
        builder.attach(method_index, children)

    return builder.build(path)


# --- Cohesive classes --------------------------------------------------------


def test_all_methods_sharing_one_field_is_cohesive() -> None:
    entry = build_class(
        "counter.py",
        "Counter",
        {
            "increment": (["count"], []),
            "decrement": (["count"], []),
            "reset": (["count"], []),
        },
    )

    (cohesion,) = compute_file_cohesion(entry)

    assert cohesion.class_name == "Counter"
    assert cohesion.method_count == 3
    assert cohesion.field_count == 1
    assert cohesion.lcom4 == 1
    assert cohesion.is_cohesive


def test_transitive_field_sharing_is_cohesive() -> None:
    """a-b share x, b-c share y. a and c share nothing directly.

    LCOM4 counts connected components, so the chain holds them together — which
    is the correct answer, since b genuinely couples a and c through state.
    """
    entry = build_class(
        "chain.py",
        "Chain",
        {
            "a": (["x"], []),
            "b": (["x", "y"], []),
            "c": (["y"], []),
        },
    )

    (cohesion,) = compute_file_cohesion(entry)

    assert cohesion.lcom4 == 1
    assert cohesion.is_cohesive


def test_delegation_is_held_together_by_the_call_edge() -> None:
    """A method touching no fields but calling another is not isolated.

    Under LCOM1 it appears unrelated to everything; the call edge is what makes
    LCOM4 give the truthful answer.
    """
    entry = build_class(
        "delegate.py",
        "Facade",
        {
            "run": ([], ["do_work"]),
            "do_work": (["state"], []),
            "reset": (["state"], []),
        },
    )

    (cohesion,) = compute_file_cohesion(entry)

    assert cohesion.lcom4 == 1


# --- Incohesive classes ------------------------------------------------------


def test_two_unrelated_method_groups_split_cleanly() -> None:
    entry = build_class(
        "mixed.py",
        "UserAndEmail",
        {
            "load_user": (["user_id"], []),
            "save_user": (["user_id"], []),
            "send_email": (["smtp"], []),
            "queue_email": (["smtp"], []),
        },
    )

    (cohesion,) = compute_file_cohesion(entry)

    assert cohesion.lcom4 == 2
    assert not cohesion.is_cohesive
    assert cohesion.suggested_split == 2


def test_components_name_the_methods_in_each_group() -> None:
    """The score says how many classes; the components say which methods.

    That is what makes LCOM4 actionable rather than merely diagnostic.
    """
    entry = build_class(
        "mixed.py",
        "TwoJobs",
        {
            "a1": (["x"], []),
            "a2": (["x"], []),
            "b1": (["y"], []),
        },
    )

    (cohesion,) = compute_file_cohesion(entry)

    groups = {frozenset(component) for component in cohesion.components}
    assert groups == {frozenset({"a1", "a2"}), frozenset({"b1"})}


def test_components_are_ordered_largest_first() -> None:
    entry = build_class(
        "mixed.py",
        "Uneven",
        {
            "a1": (["x"], []),
            "a2": (["x"], []),
            "a3": (["x"], []),
            "b1": (["y"], []),
        },
    )

    (cohesion,) = compute_file_cohesion(entry)

    assert len(cohesion.components[0]) == 3
    assert len(cohesion.components[1]) == 1


def test_methods_touching_no_fields_are_separate_components() -> None:
    entry = build_class(
        "utils.py",
        "Utilities",
        {"one": ([], []), "two": ([], []), "three": ([], [])},
    )

    (cohesion,) = compute_file_cohesion(entry)

    assert cohesion.lcom4 == 3
    assert not cohesion.is_cohesive


# --- LCOM1 -------------------------------------------------------------------


def test_lcom1_is_zero_when_every_pair_shares() -> None:
    entry = build_class(
        "c.py", "C", {"a": (["x"], []), "b": (["x"], []), "c": (["x"], [])}
    )

    (cohesion,) = compute_file_cohesion(entry)

    assert cohesion.lcom1 == 0


def test_lcom1_counts_disjoint_pairs_minus_sharing_pairs() -> None:
    """Three methods, none sharing: 3 disjoint pairs, 0 sharing. LCOM1 = 3."""
    entry = build_class(
        "c.py", "C", {"a": (["x"], []), "b": (["y"], []), "c": (["z"], [])}
    )

    (cohesion,) = compute_file_cohesion(entry)

    assert cohesion.lcom1 == 3


def test_lcom1_floors_at_zero() -> None:
    """The raw formula goes negative when sharing pairs dominate.

    A negative lack-of-cohesion is not meaningful; it means very cohesive,
    which zero already expresses.
    """
    entry = build_class(
        "c.py",
        "C",
        {"a": (["x"], []), "b": (["x"], []), "c": (["x"], []), "d": (["x"], [])},
    )

    (cohesion,) = compute_file_cohesion(entry)

    assert cohesion.lcom1 == 0


def test_lcom1_and_lcom4_can_disagree() -> None:
    """High LCOM1 with LCOM4 of 1 usually means accessors, not a design flaw.

    The disagreement is itself the signal, which is why both are reported.
    """
    entry = build_class(
        "c.py",
        "C",
        {
            "get_a": (["a"], []),
            "get_b": (["b"], []),
            "get_c": (["c"], []),
            "get_d": (["d"], []),
            "compute": (["a", "b", "c", "d"], []),
        },
    )

    (cohesion,) = compute_file_cohesion(entry)

    assert cohesion.lcom1 > 0
    assert cohesion.lcom4 == 1


# --- Trivial and edge cases --------------------------------------------------


def test_single_method_class_is_trivially_cohesive() -> None:
    """There is no pair of methods that could fail to relate.

    Flagging these would bury real findings in noise.
    """
    entry = build_class("c.py", "Tiny", {"only": (["x"], [])})

    (cohesion,) = compute_file_cohesion(entry)

    assert cohesion.lcom4 == 1
    assert cohesion.is_cohesive


def test_class_with_no_methods() -> None:
    builder = ArenaBuilder()
    root = builder.add(AstNodeKind.FILE)
    class_index = builder.add(AstNodeKind.CLASS, "Empty", parent=root)
    builder.attach(root, [class_index])

    (cohesion,) = compute_file_cohesion(builder.build("c.py"))

    assert cohesion.method_count == 0
    assert cohesion.lcom4 == 0


def test_inheritance_suffix_is_stripped_from_the_class_name() -> None:
    """The Sample Engine encodes bases as 'Derived : Base'."""
    entry = build_class("c.py", "Derived : Base", {"a": (["x"], []), "b": (["x"], [])})

    (cohesion,) = compute_file_cohesion(entry)

    assert cohesion.class_name == "Derived"


def test_nested_classes_are_measured_separately() -> None:
    """A nested class's methods belong to it, not to the outer class.

    Attributing them upward would merge two classes' cohesion into one
    misleading figure.
    """
    builder = ArenaBuilder()
    root = builder.add(AstNodeKind.FILE)
    outer = builder.add(AstNodeKind.CLASS, "Outer", parent=root)
    builder.attach(root, [outer])

    outer_method = builder.add(AstNodeKind.FUNCTION, "outer_method", parent=outer)
    inner = builder.add(AstNodeKind.CLASS, "Inner", parent=outer)
    builder.attach(outer, [outer_method, inner])

    inner_method = builder.add(AstNodeKind.FUNCTION, "inner_method", parent=inner)
    builder.attach(inner, [inner_method])

    results = {c.class_name: c for c in compute_file_cohesion(builder.build("c.py"))}

    assert results["Outer"].method_count == 1
    assert results["Inner"].method_count == 1


def test_file_with_no_classes_yields_nothing() -> None:
    builder = ArenaBuilder()
    builder.add(AstNodeKind.FILE)

    assert compute_file_cohesion(builder.build("empty.py")) == []


# --- Whole-snapshot computation ----------------------------------------------


def test_cohesion_across_multiple_files() -> None:
    entries = [
        build_class("a.py", "A", {"m1": (["x"], []), "m2": (["x"], [])}),
        build_class("b.py", "B", {"m1": (["x"], []), "m2": (["y"], [])}),
    ]

    results = compute_cohesion(entries)

    assert len(results) == 2
    assert {c.class_name for c in results} == {"A", "B"}


def test_results_are_sorted_worst_first() -> None:
    entries = [
        build_class("good.py", "Good", {"m1": (["x"], []), "m2": (["x"], [])}),
        build_class(
            "bad.py",
            "Bad",
            {"m1": (["x"], []), "m2": (["y"], []), "m3": (["z"], [])},
        ),
    ]

    results = compute_cohesion(entries)

    assert results[0].class_name == "Bad"
    assert results[0].lcom4 > results[1].lcom4


def test_empty_snapshot_yields_nothing() -> None:
    assert compute_cohesion([]) == []
