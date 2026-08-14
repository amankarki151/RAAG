"""Resolution of import statements to files inside the repository.

This is where the dependency graph's accuracy is actually decided, and where
its main limitation lives.

An import names a target the way the *build system* would find it — a header on
an include path, a module on ``sys.path``. RAAG has neither. It has a list of
files it parsed, and a string. Resolution is therefore a suffix match against
that file list, with tie-break rules for the cases where several files could
plausibly answer.

The alternative is a real semantic index: a compile_commands.json for C++, an
import graph honouring sys.path and package layout for Python. That is the
correct long-term answer and it is on the roadmap. It is not achievable from
syntax alone, which is what this layer has.

What follows is deliberately conservative. Where the evidence is thin, an
import is reported as external rather than guessed at — a fabricated edge
distorts every metric computed downstream, while a missing one only understates
coupling.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from raag_tune.graph_types import Resolution

__all__ = ["ModuleIndex", "ResolutionOutcome"]


@dataclass(frozen=True, slots=True)
class ResolutionOutcome:
    """The result of matching one import against the repository."""

    target: str
    resolution: Resolution
    candidates: int = 1


def _normalise(path: str) -> str:
    """Force forward slashes so Windows-written snapshots match on any host."""
    return path.replace("\\", "/")


def _suffixes(path: str) -> list[str]:
    """Every trailing path fragment, longest first.

    ``src/raag/foo.hpp`` yields ``src/raag/foo.hpp``, ``raag/foo.hpp``,
    ``foo.hpp``. Indexing all of them lets an include written as any of those
    forms find the file, which is what real include paths do.
    """
    parts = PurePosixPath(_normalise(path)).parts
    return ["/".join(parts[i:]) for i in range(len(parts))]


@dataclass(slots=True)
class ModuleIndex:
    """Lookup from import text to repository file path.

    Built once per snapshot and queried per import. The index maps every path
    suffix to the files that end with it, so a lookup is a dictionary hit
    rather than a scan over the file list — which matters when a repository has
    thousands of files and tens of thousands of imports.
    """

    _by_suffix: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    _known_paths: set[str] = field(default_factory=set)

    @classmethod
    def from_paths(cls, paths: list[str]) -> ModuleIndex:
        index = cls()
        for raw_path in paths:
            path = _normalise(raw_path)
            index._known_paths.add(path)
            for suffix in _suffixes(path):
                index._by_suffix[suffix].append(path)
        return index

    def __len__(self) -> int:
        return len(self._known_paths)

    def __contains__(self, path: str) -> bool:
        return _normalise(path) in self._known_paths

    def resolve_cpp_include(self, raw: str, from_file: str) -> ResolutionOutcome:
        """Resolve a ``#include`` target.

        The include text arrives already stripped of its delimiters. A relative
        include is tried against the including file's directory first, since
        that is where the compiler would look before searching include paths.
        """
        target = _normalise(raw.strip())
        if not target:
            return ResolutionOutcome(raw, Resolution.EXTERNAL, candidates=0)

        # Same-directory resolution: "detail/impl.hpp" from src/core/a.cpp is
        # most likely src/core/detail/impl.hpp, not a same-named file elsewhere.
        sibling = _normalise(str(PurePosixPath(from_file).parent / target))
        if sibling in self._known_paths:
            return ResolutionOutcome(sibling, Resolution.RESOLVED)

        return self._match_suffix(target, from_file, raw)

    def resolve_python_import(self, raw: str, from_file: str) -> ResolutionOutcome:
        """Resolve an ``import`` or ``from ... import`` target.

        Dotted module names become path fragments. Both ``pkg/mod.py`` and
        ``pkg/mod/__init__.py`` are tried, since a dotted name addresses either
        a module or a package.
        """
        target = raw.strip()
        if not target:
            return ResolutionOutcome(raw, Resolution.EXTERNAL, candidates=0)

        # A leading dot is a relative import: resolve against the importing
        # package rather than the repository root. Each additional dot climbs
        # one more level.
        if target.startswith("."):
            depth = len(target) - len(target.lstrip("."))
            remainder = target[depth:].replace(".", "/")
            base = PurePosixPath(from_file).parent
            for _ in range(depth - 1):
                base = base.parent
            candidate_stem = (
                _normalise(str(base / remainder)) if remainder else str(base)
            )
        else:
            candidate_stem = target.replace(".", "/")

        for suffix in (f"{candidate_stem}.py", f"{candidate_stem}/__init__.py"):
            if suffix in self._known_paths:
                return ResolutionOutcome(suffix, Resolution.RESOLVED)

        return self._match_suffix(f"{candidate_stem}.py", from_file, raw)

    def _match_suffix(self, target: str, from_file: str, raw: str) -> ResolutionOutcome:
        """Suffix lookup with tie-breaking.

        A single match resolves. Several matches are ambiguous — the closest
        one by shared directory prefix wins, since a file is more likely to
        include a neighbour than a same-named file across the tree. No match at
        all means the target is outside the repository.
        """
        candidates = self._by_suffix.get(target)
        if not candidates:
            return ResolutionOutcome(raw, Resolution.EXTERNAL, candidates=0)

        if len(candidates) == 1:
            return ResolutionOutcome(candidates[0], Resolution.RESOLVED)

        best = max(candidates, key=lambda path: _shared_prefix_length(path, from_file))
        return ResolutionOutcome(best, Resolution.AMBIGUOUS, candidates=len(candidates))


def _shared_prefix_length(left: str, right: str) -> int:
    """Number of leading path components two paths have in common."""
    left_parts = PurePosixPath(left).parts
    right_parts = PurePosixPath(right).parts

    shared = 0
    for a, b in zip(left_parts, right_parts, strict=False):
        if a != b:
            break
        shared += 1
    return shared
