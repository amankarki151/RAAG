"""Tests for the binary snapshot reader.

Snapshots are constructed here with ``struct.pack`` rather than by running the
C++ engine. Two reasons: the suite must pass in CI where the C++ engine may not
have been built, and an independently written encoder is a genuine check on the
decoder. If both were generated from the same code they could be wrong in the
same way and the tests would pass anyway.
"""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from raag_tune.ast_types import AstArena, AstNode, AstNodeKind
from raag_tune.snapshot_reader import (
    MAGIC,
    SUPPORTED_SCHEMA_VERSION,
    InvalidMagicError,
    SnapshotError,
    TruncatedSnapshotError,
    UnsupportedSchemaVersionError,
    read_snapshot,
    read_snapshot_header,
)

# --- Encoding helpers --------------------------------------------------------


def encode_string(value: str) -> bytes:
    data = value.encode("utf-8")
    return struct.pack("<I", len(data)) + data


def encode_node_record(
    kind: int,
    name: str,
    byte_start: int,
    byte_end: int,
    first_child_index: int,
    child_count: int,
    parent_index: int,
) -> bytes:
    """Encode one node exactly as specified in docs/CONTRACTS.md."""
    return (
        struct.pack("<B", kind)
        + encode_string(name)
        + struct.pack("<I", byte_start)
        + struct.pack("<I", byte_end)
        + struct.pack("<I", first_child_index)
        + struct.pack("<I", child_count)
        + struct.pack("<i", parent_index)
    )


def encode_file_record(path: str, nodes: list[bytes]) -> bytes:
    return encode_string(path) + struct.pack("<I", len(nodes)) + b"".join(nodes)


def encode_snapshot(
    files: list[bytes],
    *,
    magic: bytes = MAGIC,
    version: int = SUPPORTED_SCHEMA_VERSION,
) -> bytes:
    return (
        magic
        + struct.pack("<I", version)
        + struct.pack("<I", len(files))
        + b"".join(files)
    )


def sample_nodes() -> list[bytes]:
    """A root with two children, covering every field with distinct values."""
    return [
        encode_node_record(AstNodeKind.FILE, "", 0, 120, 1, 2, -1),
        encode_node_record(AstNodeKind.CLASS, "Widget", 0, 60, 0, 0, 0),
        encode_node_record(AstNodeKind.FUNCTION, "render", 61, 120, 0, 0, 0),
    ]


def write(tmp_path: Path, data: bytes, name: str = "snapshot.raag.bin") -> Path:
    target = tmp_path / name
    target.write_bytes(data)
    return target


# --- Round-trip --------------------------------------------------------------


def test_reads_every_node_field(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        encode_snapshot([encode_file_record("src/widget.cpp", sample_nodes())]),
    )

    entries = read_snapshot(path)

    assert len(entries) == 1
    assert entries[0].path == "src/widget.cpp"

    nodes = entries[0].arena.nodes
    assert len(nodes) == 3

    assert nodes[0] == AstNode(AstNodeKind.FILE, "", 0, 120, 1, 2, -1)
    assert nodes[1] == AstNode(AstNodeKind.CLASS, "Widget", 0, 60, 0, 0, 0)
    assert nodes[2] == AstNode(AstNodeKind.FUNCTION, "render", 61, 120, 0, 0, 0)


def test_reads_multiple_file_entries_in_order(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        encode_snapshot(
            [
                encode_file_record("a.cpp", sample_nodes()),
                encode_file_record("nested/dir/b.py", sample_nodes()),
                encode_file_record("c.hpp", sample_nodes()),
            ]
        ),
    )

    entries = read_snapshot(path)

    assert [entry.path for entry in entries] == ["a.cpp", "nested/dir/b.py", "c.hpp"]
    assert all(len(entry.arena) == 3 for entry in entries)


def test_root_parent_index_survives_as_negative_one(tmp_path: Path) -> None:
    """The -1 sentinel must not be read back as 4294967295.

    A reader using an unsigned format string would produce exactly that, and
    every downstream root check would silently fail.
    """
    path = write(
        tmp_path, encode_snapshot([encode_file_record("root.cpp", sample_nodes())])
    )

    root = read_snapshot(path)[0].arena.nodes[0]

    assert root.parent_index == -1
    assert root.is_root


def test_preserves_non_ascii_names(tmp_path: Path) -> None:
    nodes = [
        encode_node_record(AstNodeKind.FILE, "", 0, 40, 1, 2, -1),
        encode_node_record(AstNodeKind.FUNCTION, "calculer_été", 0, 20, 0, 0, 0),
        encode_node_record(AstNodeKind.CLASS, "Ünïcödé", 21, 40, 0, 0, 0),
    ]
    path = write(tmp_path, encode_snapshot([encode_file_record("unicode.py", nodes)]))

    arena = read_snapshot(path)[0].arena

    assert arena.nodes[1].name == "calculer_été"
    assert arena.nodes[2].name == "Ünïcödé"


def test_empty_name_reads_as_empty_string(tmp_path: Path) -> None:
    """Empty is meaningful: the extractor could not resolve a name cleanly."""
    path = write(
        tmp_path, encode_snapshot([encode_file_record("x.cpp", sample_nodes())])
    )

    name = read_snapshot(path)[0].arena.nodes[0].name

    assert name == ""
    assert name is not None


def test_snapshot_with_no_files(tmp_path: Path) -> None:
    path = write(tmp_path, encode_snapshot([]))
    assert read_snapshot(path) == []


def test_file_with_no_nodes(tmp_path: Path) -> None:
    path = write(tmp_path, encode_snapshot([encode_file_record("empty.py", [])]))

    entries = read_snapshot(path)

    assert len(entries) == 1
    assert len(entries[0].arena) == 0
    assert entries[0].arena.root() is None


def test_reads_all_node_kinds(tmp_path: Path) -> None:
    nodes = [
        encode_node_record(kind, f"n{kind}", 0, 1, 0, 0, -1 if kind == 0 else 0)
        for kind in AstNodeKind
    ]
    path = write(tmp_path, encode_snapshot([encode_file_record("kinds.cpp", nodes)]))

    arena = read_snapshot(path)[0].arena

    assert [node.kind for node in arena] == list(AstNodeKind)


# --- Header ------------------------------------------------------------------


def test_header_reads_without_parsing_body(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        encode_snapshot([encode_file_record("a.cpp", sample_nodes())] * 4),
    )

    version, file_count = read_snapshot_header(path)

    assert version == SUPPORTED_SCHEMA_VERSION
    assert file_count == 4


def test_header_rejects_bad_magic(tmp_path: Path) -> None:
    path = write(tmp_path, encode_snapshot([], magic=b"NOPE"))

    with pytest.raises(InvalidMagicError):
        read_snapshot_header(path)


# --- Failure handling --------------------------------------------------------


def test_wrong_magic_raises(tmp_path: Path) -> None:
    path = write(tmp_path, encode_snapshot([], magic=b"XXXX"))

    with pytest.raises(InvalidMagicError) as excinfo:
        read_snapshot(path)

    assert excinfo.value.found == b"XXXX"


def test_unsupported_version_raises_with_both_versions(tmp_path: Path) -> None:
    path = write(tmp_path, encode_snapshot([], version=999))

    with pytest.raises(UnsupportedSchemaVersionError) as excinfo:
        read_snapshot(path)

    assert excinfo.value.found == 999
    assert excinfo.value.expected == SUPPORTED_SCHEMA_VERSION
    # The message must name both, or the reader is unactionable.
    assert "999" in str(excinfo.value)


def test_truncated_mid_node_raises(tmp_path: Path) -> None:
    complete = encode_snapshot([encode_file_record("a.cpp", sample_nodes())])
    path = write(tmp_path, complete[: len(complete) - 12])

    with pytest.raises(TruncatedSnapshotError):
        read_snapshot(path)


def test_truncated_header_raises(tmp_path: Path) -> None:
    path = write(tmp_path, MAGIC + b"\x01\x00")

    with pytest.raises(TruncatedSnapshotError):
        read_snapshot(path)


def test_empty_file_raises(tmp_path: Path) -> None:
    path = write(tmp_path, b"")

    with pytest.raises(SnapshotError):
        read_snapshot(path)


def test_missing_file_raises_file_not_found(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        read_snapshot(tmp_path / "does_not_exist.raag.bin")


def test_out_of_range_kind_raises(tmp_path: Path) -> None:
    nodes = [encode_node_record(200, "bogus", 0, 1, 0, 0, -1)]
    path = write(tmp_path, encode_snapshot([encode_file_record("bad.cpp", nodes)]))

    with pytest.raises(SnapshotError, match="unknown node kind"):
        read_snapshot(path)


def test_implausible_string_length_raises(tmp_path: Path) -> None:
    """A corrupt length must be rejected, not used to size an allocation."""
    corrupt = (
        MAGIC
        + struct.pack("<I", SUPPORTED_SCHEMA_VERSION)
        + struct.pack("<I", 1)
        + struct.pack("<I", 0xFFFFFFF0)  # path_length
    )
    path = write(tmp_path, corrupt)

    with pytest.raises(SnapshotError):
        read_snapshot(path)


def test_node_count_larger_than_file_raises(tmp_path: Path) -> None:
    """A count that overstates the data must fail rather than short-read."""
    body = encode_string("a.cpp") + struct.pack("<I", 500) + b"".join(sample_nodes())
    path = write(tmp_path, encode_snapshot([body]))

    with pytest.raises(TruncatedSnapshotError):
        read_snapshot(path)


# --- Arena behaviour ---------------------------------------------------------


def build_arena() -> AstArena:
    return AstArena(
        nodes=[
            AstNode(AstNodeKind.FILE, "", 0, 200, 1, 2, -1),
            AstNode(AstNodeKind.CLASS, "Outer", 0, 100, 3, 2, 0),
            AstNode(AstNodeKind.FUNCTION, "standalone", 101, 200, 0, 0, 0),
            AstNode(AstNodeKind.FUNCTION, "method_a", 10, 50, 0, 0, 1),
            AstNode(AstNodeKind.FUNCTION, "method_b", 51, 100, 0, 0, 1),
        ]
    )


def test_children_returns_contiguous_slice() -> None:
    arena = build_arena()

    root_children = arena.children(0)
    assert [node.name for node in root_children] == ["Outer", "standalone"]

    class_children = arena.children(1)
    assert [node.name for node in class_children] == ["method_a", "method_b"]

    assert arena.children(3) == []


def test_child_indices_matches_children() -> None:
    arena = build_arena()

    for index in range(len(arena)):
        by_index = [arena[i] for i in arena.child_indices(index)]
        assert by_index == arena.children(index)


def test_walk_visits_every_node_once_with_depths() -> None:
    arena = build_arena()

    visited = list(arena.walk())

    assert len(visited) == len(arena)
    assert {node.name for node, _ in visited} == {
        "",
        "Outer",
        "standalone",
        "method_a",
        "method_b",
    }

    depths = {node.name: depth for node, depth in visited}
    assert depths[""] == 0
    assert depths["Outer"] == 1
    assert depths["standalone"] == 1
    assert depths["method_a"] == 2
    assert depths["method_b"] == 2


def test_walk_yields_siblings_in_source_order() -> None:
    arena = build_arena()

    names = [node.name for node, _ in arena.walk()]

    assert names.index("Outer") < names.index("standalone")
    assert names.index("method_a") < names.index("method_b")


def test_walk_from_subtree_root() -> None:
    arena = build_arena()

    names = [node.name for node, _ in arena.walk(start=1)]

    assert names == ["Outer", "method_a", "method_b"]


def test_walk_on_empty_arena_yields_nothing() -> None:
    assert list(AstArena().walk()) == []


def test_count_by_kind_includes_zero_counts() -> None:
    counts = build_arena().count_by_kind()

    assert counts[AstNodeKind.FILE] == 1
    assert counts[AstNodeKind.CLASS] == 1
    assert counts[AstNodeKind.FUNCTION] == 3
    assert counts[AstNodeKind.IMPORT] == 0
    assert set(counts) == set(AstNodeKind)


def test_nodes_of_kind_filters() -> None:
    functions = build_arena().nodes_of_kind(AstNodeKind.FUNCTION)

    assert [node.name for node in functions] == [
        "standalone",
        "method_a",
        "method_b",
    ]


def test_node_properties() -> None:
    arena = build_arena()

    assert arena[0].is_root
    assert not arena[1].is_root
    assert arena[3].is_leaf
    assert not arena[0].is_leaf
    assert arena[1].byte_length == 100


def test_nodes_are_immutable() -> None:
    node = build_arena()[0]

    with pytest.raises(AttributeError):
        node.name = "mutated"  # type: ignore[misc]
