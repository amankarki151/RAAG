"""Python-side representation of the AST produced by the Sample Engine.

These types mirror their C++ counterparts exactly. The mirroring is deliberate
rather than incidental: the snapshot format is a byte-level contract, and any
divergence between the two definitions — a reordered enum member, a renamed
field — produces a snapshot that loads without error and means something
different. See docs/CONTRACTS.md.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from enum import IntEnum

__all__ = ["AstArena", "AstNode", "AstNodeKind", "SnapshotEntry"]


class AstNodeKind(IntEnum):
    """Structural classification of a node, normalised across languages.

    Values are fixed by the wire format and must match the C++
    ``enum class AstNodeKind : uint8_t`` member for member. Reordering these,
    or inserting a member anywhere but the end, silently reinterprets every
    existing snapshot.

    Deliberately coarse: RAAG reasons about architecture, not syntax, so it
    needs to know something is a function without caring whether the grammar
    called it ``function_definition`` or ``function_declaration``.
    """

    FILE = 0
    CLASS = 1
    FUNCTION = 2
    IMPORT = 3
    CALL_EXPRESSION = 4
    VARIABLE = 5
    OTHER = 6

    def __str__(self) -> str:
        return _KIND_NAMES[self]


_KIND_NAMES: dict[AstNodeKind, str] = {
    AstNodeKind.FILE: "File",
    AstNodeKind.CLASS: "Class",
    AstNodeKind.FUNCTION: "Function",
    AstNodeKind.IMPORT: "Import",
    AstNodeKind.CALL_EXPRESSION: "Call",
    AstNodeKind.VARIABLE: "Variable",
    AstNodeKind.OTHER: "Other",
}


@dataclass(frozen=True, slots=True)
class AstNode:
    """A single node in an arena.

    Frozen because a parsed AST is a fact about a file at a point in time.
    Nothing downstream has cause to mutate one, and freezing turns an
    accidental write into an error at the point it happens rather than a
    confusing result several stages later.

    ``slots=True`` matters at this scale. A per-instance ``__dict__`` costs
    roughly a hundred bytes on top of the fields themselves; across the million
    nodes a mid-size repository produces, that is most of a gigabyte spent on
    nothing.

    Attributes:
        kind: Normalised structural classification.
        name: Symbol name where one exists, empty otherwise. Many nodes are
            structural and carry no identifier. An empty name means the
            extractor could not resolve one cleanly — it declines to guess,
            because a wrong name becomes a confidently wrong dependency edge.
        byte_start: Offset of the node's first byte in the source file.
        byte_end: Offset one past the node's last byte.
        first_child_index: Index of the first child in the owning arena.
            Meaningless when ``child_count`` is zero.
        child_count: Number of children, stored contiguously from
            ``first_child_index``. The contiguity is guaranteed by the
            builder's breadth-first walk; it is what makes an index range a
            valid way to refer to children at all.
        parent_index: Index of the parent, or ``-1`` for the root.
    """

    kind: AstNodeKind
    name: str
    byte_start: int
    byte_end: int
    first_child_index: int
    child_count: int
    parent_index: int

    @property
    def is_root(self) -> bool:
        return self.parent_index == -1

    @property
    def is_leaf(self) -> bool:
        return self.child_count == 0

    @property
    def byte_length(self) -> int:
        return self.byte_end - self.byte_start


@dataclass(slots=True)
class AstArena:
    """All nodes for one source file, in a flat list.

    Index 0 is the root. Children are referenced by index range rather than by
    object reference, matching the C++ representation this is loaded from.
    """

    nodes: list[AstNode] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.nodes)

    def __iter__(self) -> Iterator[AstNode]:
        return iter(self.nodes)

    def __getitem__(self, index: int) -> AstNode:
        return self.nodes[index]

    def root(self) -> AstNode | None:
        """The root node, or None for an empty arena."""
        return self.nodes[0] if self.nodes else None

    def children(self, index: int) -> list[AstNode]:
        """Children of the node at ``index``, in source order."""
        node = self.nodes[index]
        if node.child_count == 0:
            return []
        start = node.first_child_index
        return self.nodes[start : start + node.child_count]

    def child_indices(self, index: int) -> range:
        """Indices of the node's children.

        Preferred over :meth:`children` when the caller needs indices — for
        instance to record graph edges — since it avoids materialising a list.
        """
        node = self.nodes[index]
        return range(node.first_child_index, node.first_child_index + node.child_count)

    def walk(self, start: int = 0) -> Iterator[tuple[AstNode, int]]:
        """Yield every node in the subtree at ``start`` with its depth.

        Depth-first in source order. Uses an explicit stack rather than
        recursion: nesting depth is a property of input the tool does not
        control, and Python's default recursion limit is low enough that a
        heavily nested file would raise before finishing.
        """
        if not self.nodes:
            return

        stack: list[tuple[int, int]] = [(start, 0)]
        while stack:
            index, depth = stack.pop()
            node = self.nodes[index]
            yield node, depth

            # Pushed in reverse so siblings pop in source order.
            for child_index in reversed(self.child_indices(index)):
                stack.append((child_index, depth + 1))

    def count_by_kind(self) -> dict[AstNodeKind, int]:
        """Node totals per kind, including kinds with a count of zero."""
        counts = dict.fromkeys(AstNodeKind, 0)
        for node in self.nodes:
            counts[node.kind] += 1
        return counts

    def nodes_of_kind(self, kind: AstNodeKind) -> list[AstNode]:
        return [node for node in self.nodes if node.kind == kind]


@dataclass(slots=True)
class SnapshotEntry:
    """One source file's path and parsed contents."""

    path: str
    arena: AstArena
