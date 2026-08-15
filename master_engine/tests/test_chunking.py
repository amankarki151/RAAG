"""Tests for AST-boundary chunking.

Source text and AST are constructed together so byte offsets are exact rather
than approximate. A test whose offsets are slightly wrong would pass while the
chunker returned subtly misaligned code, which is precisely the bug this layer
exists to avoid.
"""

from __future__ import annotations

from raag_master.chunking import ChunkingConfig, chunk_entry, chunk_snapshot
from raag_tune.ast_types import AstArena, AstNode, AstNodeKind, SnapshotEntry


def node(
    kind: AstNodeKind,
    name: str,
    source: str,
    fragment: str,
    *,
    parent: int = 0,
) -> AstNode:
    """A node whose byte range is located by finding ``fragment`` in ``source``."""
    start = source.index(fragment)
    return AstNode(
        kind=kind,
        name=name,
        byte_start=start,
        byte_end=start + len(fragment.encode("utf-8")),
        first_child_index=0,
        child_count=0,
        parent_index=parent,
    )


def entry_from(path: str, source: str, *children: AstNode) -> SnapshotEntry:
    root = AstNode(
        kind=AstNodeKind.FILE,
        name="",
        byte_start=0,
        byte_end=len(source.encode("utf-8")),
        first_child_index=1,
        child_count=len(children),
        parent_index=-1,
    )
    return SnapshotEntry(path=path, arena=AstArena(nodes=[root, *children]))


LONG_BODY = "\n".join(f"    line_{i} = compute_value({i})" for i in range(8))

PYTHON_SOURCE = f"""import os


def load_config(path):
{LONG_BODY}
    return path


def save_config(path, data):
{LONG_BODY}
    return True
"""


def python_entry() -> SnapshotEntry:
    load = f"def load_config(path):\n{LONG_BODY}\n    return path"
    save = f"def save_config(path, data):\n{LONG_BODY}\n    return True"
    return entry_from(
        "config.py",
        PYTHON_SOURCE,
        node(AstNodeKind.FUNCTION, "load_config", PYTHON_SOURCE, load),
        node(AstNodeKind.FUNCTION, "save_config", PYTHON_SOURCE, save),
    )


# --- Boundaries --------------------------------------------------------------


def test_each_function_becomes_one_chunk():
    chunks = chunk_entry(python_entry(), PYTHON_SOURCE)

    assert [chunk.name for chunk in chunks] == ["load_config", "save_config"]


def test_chunk_text_matches_the_source_exactly():
    """The whole point of AST boundaries: no partial declarations.

    Half a function retrieved as context is worse than none — it reads as
    complete and is missing the part that mattered.
    """
    chunks = chunk_entry(python_entry(), PYTHON_SOURCE)

    for chunk in chunks:
        assert chunk.text == PYTHON_SOURCE[chunk.byte_start : chunk.byte_end]
        assert chunk.text.startswith("def ")


def test_chunks_are_ordered_by_position():
    chunks = chunk_entry(python_entry(), PYTHON_SOURCE)

    starts = [chunk.byte_start for chunk in chunks]
    assert starts == sorted(starts)


def test_line_numbers_are_one_based_and_correct():
    chunks = chunk_entry(python_entry(), PYTHON_SOURCE)
    first = chunks[0]

    lines = PYTHON_SOURCE.splitlines()
    assert lines[first.line_start - 1].startswith("def load_config")
    assert first.line_count == first.text.count("\n") + 1


def test_byte_offsets_survive_non_ascii_source():
    """Offsets are byte offsets; slicing a str by them misplaces every
    boundary after the first multi-byte character."""
    source = (
        'def greet():\n    return "héllo wörld — a longer body for the size floor"\n'
    )
    fragment = source[source.index("def greet") :].rstrip("\n")

    entry = entry_from(
        "greet.py", source, node(AstNodeKind.FUNCTION, "greet", source, fragment)
    )

    (chunk,) = chunk_entry(entry, source, ChunkingConfig(min_bytes=10))

    assert "héllo" in chunk.text
    assert chunk.text.startswith("def greet")


# --- Size bounds -------------------------------------------------------------


def test_chunks_below_the_floor_are_dropped():
    """A one-line getter carries nothing a retriever can use, and hundreds of
    them crowd out the chunks that would have answered the query."""
    source = "def x():\n    return 1\n"
    entry = entry_from(
        "tiny.py",
        source,
        node(AstNodeKind.FUNCTION, "x", source, "def x():\n    return 1"),
    )

    assert chunk_entry(entry, source, ChunkingConfig(min_bytes=80)) == []


def test_chunks_above_the_ceiling_are_dropped():
    chunks = chunk_entry(python_entry(), PYTHON_SOURCE, ChunkingConfig(max_bytes=50))

    assert chunks == []


def test_bounds_are_configurable():
    permissive = chunk_entry(
        python_entry(), PYTHON_SOURCE, ChunkingConfig(min_bytes=1, max_bytes=100_000)
    )

    assert len(permissive) == 2


# --- Classes and qualified names ---------------------------------------------


def build_class_source() -> tuple[str, SnapshotEntry]:
    body_a = "\n".join(f"        self.value_{i} = {i}" for i in range(6))
    body_b = "\n".join(f"        self.other_{i} = {i}" for i in range(6))
    source = (
        "class Repository:\n"
        f"    def load(self):\n{body_a}\n        return self.value_0\n\n"
        f"    def save(self):\n{body_b}\n        return True\n"
    )

    load = f"def load(self):\n{body_a}\n        return self.value_0"
    save = f"def save(self):\n{body_b}\n        return True"

    class_node = node(
        AstNodeKind.CLASS, "Repository", source, source.rstrip("\n"), parent=0
    )
    load_node = node(AstNodeKind.FUNCTION, "load", source, load, parent=1)
    save_node = node(AstNodeKind.FUNCTION, "save", source, save, parent=1)

    root = AstNode(
        kind=AstNodeKind.FILE,
        name="",
        byte_start=0,
        byte_end=len(source.encode("utf-8")),
        first_child_index=1,
        child_count=1,
        parent_index=-1,
    )
    class_with_children = AstNode(
        kind=class_node.kind,
        name=class_node.name,
        byte_start=class_node.byte_start,
        byte_end=class_node.byte_end,
        first_child_index=2,
        child_count=2,
        parent_index=0,
    )

    arena = AstArena(nodes=[root, class_with_children, load_node, save_node])
    return source, SnapshotEntry(path="repo.py", arena=arena)


def test_methods_get_qualified_names():
    source, entry = build_class_source()

    chunks = chunk_entry(entry, source)

    qualified = {chunk.qualified_name for chunk in chunks}
    assert qualified == {"Repository.load", "Repository.save"}


def test_methods_record_their_parent_class():
    source, entry = build_class_source()

    chunks = chunk_entry(entry, source)

    assert all(chunk.parent_class == "Repository" for chunk in chunks)


def test_class_with_methods_is_not_chunked_separately():
    """Its methods already cover the same bytes; indexing both returns the
    same code twice for a single query."""
    source, entry = build_class_source()

    chunks = chunk_entry(entry, source)

    assert all(chunk.kind == "function" for chunk in chunks)


def test_class_without_methods_is_chunked():
    source = (
        "class Config:\n" + "\n".join(f"    field_{i} = {i}" for i in range(8)) + "\n"
    )
    entry = entry_from(
        "config.py",
        source,
        node(AstNodeKind.CLASS, "Config", source, source.rstrip("\n")),
    )

    (chunk,) = chunk_entry(entry, source)

    assert chunk.kind == "class"
    assert chunk.qualified_name == "Config"


def test_inheritance_suffix_is_stripped_from_names():
    """The Sample Engine encodes bases as 'Derived : Base'."""
    source = (
        "class Derived(Base):\n"
        + "\n".join(f"    attr_{i} = {i}" for i in range(8))
        + "\n"
    )
    entry = entry_from(
        "d.py",
        source,
        node(AstNodeKind.CLASS, "Derived : Base", source, source.rstrip("\n")),
    )

    (chunk,) = chunk_entry(entry, source)

    assert chunk.name == "Derived"


def test_classes_can_be_excluded():
    source = "class Config:\n" + "\n".join(f"    f_{i} = {i}" for i in range(8)) + "\n"
    entry = entry_from(
        "c.py", source, node(AstNodeKind.CLASS, "Config", source, source.rstrip("\n"))
    )

    assert chunk_entry(entry, source, ChunkingConfig(include_classes=False)) == []


# --- Identity ----------------------------------------------------------------


def test_chunk_ids_are_deterministic():
    """Re-indexing unchanged code must overwrite, not duplicate."""
    first = chunk_entry(python_entry(), PYTHON_SOURCE)
    second = chunk_entry(python_entry(), PYTHON_SOURCE)

    assert [c.chunk_id for c in first] == [c.chunk_id for c in second]


def test_editing_a_function_changes_its_id():
    """A stale vector for code that no longer exists is worse than a missing
    one: it gets retrieved and read as current."""
    original = chunk_entry(python_entry(), PYTHON_SOURCE)[0]

    edited_source = PYTHON_SOURCE.replace("return path", "return path.strip()")
    edited_fragment = edited_source[
        edited_source.index("def load_config") : edited_source.index("def save_config")
    ].rstrip("\n")
    edited_entry = entry_from(
        "config.py",
        edited_source,
        node(AstNodeKind.FUNCTION, "load_config", edited_source, edited_fragment),
    )

    edited = chunk_entry(edited_entry, edited_source)[0]

    assert edited.chunk_id != original.chunk_id


def test_identical_code_in_different_files_has_different_ids():
    a = chunk_entry(python_entry(), PYTHON_SOURCE)[0]

    other = python_entry()
    other = SnapshotEntry(path="other.py", arena=other.arena)
    b = chunk_entry(other, PYTHON_SOURCE)[0]

    assert a.chunk_id != b.chunk_id


def test_embedding_text_includes_location_and_name():
    """Two identical functions in different modules are different answers.

    Without the prefix they embed to the same point and the retriever has no
    basis for preferring the relevant one.
    """
    chunk = chunk_entry(python_entry(), PYTHON_SOURCE)[0]

    text = chunk.embedding_text()

    assert "config.py" in text
    assert "load_config" in text
    assert chunk.text in text


# --- Malformed input ---------------------------------------------------------


def test_unnamed_declarations_are_skipped():
    """A chunk nobody can cite is not useful context."""
    entry = entry_from(
        "a.py",
        PYTHON_SOURCE,
        node(AstNodeKind.FUNCTION, "", PYTHON_SOURCE, "def load_config(path):"),
    )

    assert chunk_entry(entry, PYTHON_SOURCE) == []


def test_offsets_past_the_end_of_source_are_skipped():
    entry = entry_from(
        "a.py",
        PYTHON_SOURCE,
        AstNode(
            kind=AstNodeKind.FUNCTION,
            name="phantom",
            byte_start=0,
            byte_end=999_999,
            first_child_index=0,
            child_count=0,
            parent_index=0,
        ),
    )

    assert chunk_entry(entry, PYTHON_SOURCE) == []


def test_inverted_byte_range_is_skipped():
    entry = entry_from(
        "a.py",
        PYTHON_SOURCE,
        AstNode(
            kind=AstNodeKind.FUNCTION,
            name="backwards",
            byte_start=100,
            byte_end=50,
            first_child_index=0,
            child_count=0,
            parent_index=0,
        ),
    )

    assert chunk_entry(entry, PYTHON_SOURCE) == []


def test_empty_source_yields_nothing():
    assert chunk_entry(python_entry(), "") == []


def test_empty_arena_yields_nothing():
    entry = SnapshotEntry(path="a.py", arena=AstArena(nodes=[]))

    assert chunk_entry(entry, PYTHON_SOURCE) == []


# --- Whole-snapshot chunking -------------------------------------------------


def test_chunk_snapshot_reads_source_from_disk(tmp_path):
    (tmp_path / "config.py").write_text(PYTHON_SOURCE, encoding="utf-8")

    chunks, unreadable = chunk_snapshot([python_entry()], tmp_path)

    assert len(chunks) == 2
    assert unreadable == []


def test_missing_files_are_reported_not_raised(tmp_path):
    """One moved file should not abort an index over several hundred."""
    chunks, unreadable = chunk_snapshot([python_entry()], tmp_path)

    assert chunks == []
    assert unreadable == ["config.py"]


def test_readable_files_still_index_when_others_are_missing(tmp_path):
    (tmp_path / "config.py").write_text(PYTHON_SOURCE, encoding="utf-8")

    missing = SnapshotEntry(path="gone.py", arena=python_entry().arena)
    chunks, unreadable = chunk_snapshot([python_entry(), missing], tmp_path)

    assert len(chunks) == 2
    assert unreadable == ["gone.py"]


def test_empty_snapshot(tmp_path):
    chunks, unreadable = chunk_snapshot([], tmp_path)

    assert chunks == []
    assert unreadable == []


# --- Derived properties ------------------------------------------------------


def test_byte_length_matches_the_range():
    for chunk in chunk_entry(python_entry(), PYTHON_SOURCE):
        assert chunk.byte_length == chunk.byte_end - chunk.byte_start


def test_config_rejects_nothing_by_default():
    """Defaults must admit ordinary functions, or the index is empty and the
    cause is a config value nobody thinks to check."""
    chunks = chunk_entry(python_entry(), PYTHON_SOURCE, ChunkingConfig())

    assert len(chunks) == 2
