"""Tests for import resolution.

Resolution is where the dependency graph's accuracy is decided, so these tests
are written against the *rules* rather than against observed behaviour. When a
rule changes, a test should fail and force the change to be deliberate.
"""

from __future__ import annotations

from raag_tune.graph_types import Resolution
from raag_tune.module_index import ModuleIndex

CPP_REPO = [
    "src/core/engine.cpp",
    "src/core/engine.hpp",
    "src/core/detail/impl.hpp",
    "src/io/reader.cpp",
    "src/io/reader.hpp",
    "include/raag/public.hpp",
    "vendor/other/engine.hpp",
]

PYTHON_REPO = [
    "pkg/__init__.py",
    "pkg/core.py",
    "pkg/io/__init__.py",
    "pkg/io/reader.py",
    "pkg/io/writer.py",
    "tools/script.py",
]


# --- Index construction ------------------------------------------------------


def test_index_reports_size() -> None:
    index = ModuleIndex.from_paths(CPP_REPO)
    assert len(index) == len(CPP_REPO)


def test_index_membership() -> None:
    index = ModuleIndex.from_paths(CPP_REPO)

    assert "src/core/engine.cpp" in index
    assert "nowhere/missing.cpp" not in index


def test_backslash_paths_normalise() -> None:
    """A snapshot written on Windows must resolve on any host."""
    index = ModuleIndex.from_paths(["src\\core\\engine.hpp"])

    assert "src/core/engine.hpp" in index

    outcome = index.resolve_cpp_include("core/engine.hpp", "src/io/reader.cpp")
    assert outcome.resolution is Resolution.RESOLVED


# --- C++ includes ------------------------------------------------------------


def test_cpp_include_resolves_by_full_path() -> None:
    index = ModuleIndex.from_paths(CPP_REPO)

    outcome = index.resolve_cpp_include("raag/public.hpp", "src/core/engine.cpp")

    assert outcome.resolution is Resolution.RESOLVED
    assert outcome.target == "include/raag/public.hpp"


def test_cpp_include_prefers_sibling_directory() -> None:
    """A relative include resolves against the including file's directory first.

    This is what the compiler does, and it is the difference between finding
    src/core/detail/impl.hpp and finding a same-named file elsewhere.
    """
    index = ModuleIndex.from_paths([*CPP_REPO, "src/io/detail/impl.hpp"])

    outcome = index.resolve_cpp_include("detail/impl.hpp", "src/core/engine.cpp")

    assert outcome.resolution is Resolution.RESOLVED
    assert outcome.target == "src/core/detail/impl.hpp"


def test_cpp_system_include_is_external() -> None:
    index = ModuleIndex.from_paths(CPP_REPO)

    outcome = index.resolve_cpp_include("vector", "src/core/engine.cpp")

    assert outcome.resolution is Resolution.EXTERNAL
    assert outcome.candidates == 0


def test_cpp_include_from_sibling_directory_is_unambiguous() -> None:
    """A quote-include finds the includer's own directory first.

    This is compiler behaviour, so it resolves cleanly even though a same-named
    file exists elsewhere in the tree.
    """
    index = ModuleIndex.from_paths(CPP_REPO)

    outcome = index.resolve_cpp_include("engine.hpp", "src/core/engine.cpp")

    assert outcome.resolution is Resolution.RESOLVED
    assert outcome.target == "src/core/engine.hpp"


def test_cpp_ambiguous_include_prefers_closest_path() -> None:
    """Two files share a name and neither is a sibling; the nearer one wins.

    A file is more likely to include something under a shared parent than a
    same-named file across the tree. The match is still reported as ambiguous
    so a caller can see the graph contains a judgement call.
    """
    index = ModuleIndex.from_paths(CPP_REPO)

    outcome = index.resolve_cpp_include("engine.hpp", "src/io/reader.cpp")

    assert outcome.resolution is Resolution.AMBIGUOUS
    assert outcome.target == "src/core/engine.hpp"
    assert outcome.candidates == 2


def test_cpp_empty_include_is_external() -> None:
    index = ModuleIndex.from_paths(CPP_REPO)

    outcome = index.resolve_cpp_include("   ", "src/core/engine.cpp")

    assert outcome.resolution is Resolution.EXTERNAL


# --- Python imports ----------------------------------------------------------


def test_python_dotted_import_resolves_to_module() -> None:
    index = ModuleIndex.from_paths(PYTHON_REPO)

    outcome = index.resolve_python_import("pkg.io.reader", "tools/script.py")

    assert outcome.resolution is Resolution.RESOLVED
    assert outcome.target == "pkg/io/reader.py"


def test_python_package_import_resolves_to_init() -> None:
    """A dotted name can address a package as well as a module."""
    index = ModuleIndex.from_paths(PYTHON_REPO)

    outcome = index.resolve_python_import("pkg.io", "tools/script.py")

    assert outcome.resolution is Resolution.RESOLVED
    assert outcome.target == "pkg/io/__init__.py"


def test_python_stdlib_import_is_external() -> None:
    index = ModuleIndex.from_paths(PYTHON_REPO)

    outcome = index.resolve_python_import("os.path", "pkg/core.py")

    assert outcome.resolution is Resolution.EXTERNAL


def test_python_relative_import_resolves_within_package() -> None:
    index = ModuleIndex.from_paths(PYTHON_REPO)

    outcome = index.resolve_python_import(".writer", "pkg/io/reader.py")

    assert outcome.resolution is Resolution.RESOLVED
    assert outcome.target == "pkg/io/writer.py"


def test_python_parent_relative_import_climbs_one_level() -> None:
    index = ModuleIndex.from_paths(PYTHON_REPO)

    outcome = index.resolve_python_import("..core", "pkg/io/reader.py")

    assert outcome.resolution is Resolution.RESOLVED
    assert outcome.target == "pkg/core.py"


def test_python_empty_import_is_external() -> None:
    index = ModuleIndex.from_paths(PYTHON_REPO)

    outcome = index.resolve_python_import("", "pkg/core.py")

    assert outcome.resolution is Resolution.EXTERNAL


def test_empty_index_resolves_nothing() -> None:
    index = ModuleIndex.from_paths([])

    assert (
        index.resolve_cpp_include("any.hpp", "a.cpp").resolution is Resolution.EXTERNAL
    )
    assert index.resolve_python_import("any", "a.py").resolution is Resolution.EXTERNAL
