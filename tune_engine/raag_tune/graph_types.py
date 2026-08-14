"""Types describing the dependency graph.

Kept separate from graph construction so the vocabulary — what counts as a
dependency, what an unresolved import means — can be read without wading
through NetworkX calls.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

__all__ = ["Dependency", "EdgeKind", "ModuleInfo", "Resolution"]


class EdgeKind(StrEnum):
    """Why one module depends on another.

    Stored as an edge attribute so metrics can weight or filter by relationship
    type later. An inheritance edge implies far tighter coupling than a single
    function call, and collapsing them into one undifferentiated "depends on"
    throws that away.
    """

    IMPORT = "import"
    INHERITANCE = "inheritance"


class Resolution(StrEnum):
    """What happened when an import was matched against the repository.

    Tracked explicitly rather than discarded. The ratio of resolved to external
    to ambiguous imports is the honest measure of how far the dependency graph
    can be trusted, and a tool that hides it is overstating its own accuracy.
    """

    RESOLVED = "resolved"
    """Matched exactly one file in the repository."""

    EXTERNAL = "external"
    """Matched nothing. A standard library or third-party dependency."""

    AMBIGUOUS = "ambiguous"
    """Matched several files. One was chosen by the tie-break rules."""


@dataclass(frozen=True, slots=True)
class Dependency:
    """A single directed dependency between two source files.

    Attributes:
        source: Repository-relative path of the depending file.
        target: Path of the depended-upon file when resolved, otherwise the
            raw import text.
        kind: The relationship type.
        resolution: Whether the target was matched inside the repository.
        raw_target: The import exactly as written. Kept for diagnostics — an
            unresolved import is much easier to investigate when you can see
            what the author actually wrote.
    """

    source: str
    target: str
    kind: EdgeKind
    resolution: Resolution
    raw_target: str

    @property
    def is_internal(self) -> bool:
        return self.resolution is not Resolution.EXTERNAL


@dataclass(frozen=True, slots=True)
class ModuleInfo:
    """Attributes attached to each node in the dependency graph."""

    path: str
    is_external: bool
    node_count: int = 0
    class_count: int = 0
    function_count: int = 0
