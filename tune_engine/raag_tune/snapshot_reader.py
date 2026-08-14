"""Reader for the binary snapshots written by the Sample Engine.

The format is specified in docs/CONTRACTS.md. This module is the only place in
the Python codebase that knows about it; everything above consumes the types in
:mod:`raag_tune.ast_types`.
"""

from __future__ import annotations

import struct
from pathlib import Path

from raag_tune.ast_types import AstArena, AstNode, AstNodeKind, SnapshotEntry

__all__ = [
    "SUPPORTED_SCHEMA_VERSION",
    "InvalidMagicError",
    "SnapshotError",
    "TruncatedSnapshotError",
    "UnsupportedSchemaVersionError",
    "read_snapshot",
    "read_snapshot_header",
]

SUPPORTED_SCHEMA_VERSION = 1
"""Schema version this reader understands.

Independent of the platform version. A layout change breaks the Sample-to-Tune
contract and must increment this on both sides, even when the release is only a
minor version bump.
"""

MAGIC = b"RAAG"

# Little-endian throughout, never native order. The format is defined by its
# specification, not by whichever machine happened to write the file — a
# snapshot produced on one architecture must load on another.
_U8 = struct.Struct("<B")
_U32 = struct.Struct("<I")
_I32 = struct.Struct("<i")

_HEADER_SIZE = 4 + _U32.size + _U32.size

_MAX_STRING_LENGTH = 1 << 20
"""Ceiling on any length-prefixed string.

A corrupted four-byte length could otherwise ask for a multi-gigabyte
allocation from a file of a few hundred bytes. No real identifier or path
approaches this.
"""

_MAX_KIND_VALUE = max(AstNodeKind).value


class SnapshotError(Exception):
    """Base class for every snapshot loading failure."""


class InvalidMagicError(SnapshotError):
    """The file does not begin with the snapshot signature."""

    def __init__(self, found: bytes) -> None:
        super().__init__(
            f"not a RAAG snapshot: expected magic {MAGIC!r}, found {found!r}"
        )
        self.found = found


class UnsupportedSchemaVersionError(SnapshotError):
    """The file's schema version is not one this reader understands."""

    def __init__(self, found: int, expected: int) -> None:
        super().__init__(
            f"snapshot schema version {found} is not supported "
            f"(this build reads version {expected}); "
            f"regenerate the snapshot with a matching Sample Engine build"
        )
        self.found = found
        self.expected = expected


class TruncatedSnapshotError(SnapshotError):
    """The file ended partway through a record."""

    def __init__(self, offset: int, needed: int, available: int) -> None:
        super().__init__(
            f"snapshot truncated at byte {offset}: "
            f"needed {needed} bytes, {available} remain"
        )
        self.offset = offset


class _Cursor:
    """Sequential reader over an in-memory buffer.

    The whole file is read once and parsed from memory rather than through
    repeated small reads. A snapshot of a mid-size repository holds on the
    order of a million nodes, each requiring seven separate field reads; going
    to the filesystem for every one of those would dominate the runtime.
    """

    __slots__ = ("_data", "_offset")

    def __init__(self, data: bytes) -> None:
        self._data = data
        self._offset = 0

    @property
    def offset(self) -> int:
        return self._offset

    def _require(self, count: int) -> None:
        if self._offset + count > len(self._data):
            raise TruncatedSnapshotError(
                offset=self._offset,
                needed=count,
                available=len(self._data) - self._offset,
            )

    def take_bytes(self, count: int) -> bytes:
        self._require(count)
        chunk = self._data[self._offset : self._offset + count]
        self._offset += count
        return chunk

    def take_u8(self) -> int:
        self._require(_U8.size)
        (value,) = _U8.unpack_from(self._data, self._offset)
        self._offset += _U8.size
        return int(value)

    def take_u32(self) -> int:
        self._require(_U32.size)
        (value,) = _U32.unpack_from(self._data, self._offset)
        self._offset += _U32.size
        return int(value)

    def take_i32(self) -> int:
        self._require(_I32.size)
        (value,) = _I32.unpack_from(self._data, self._offset)
        self._offset += _I32.size
        return int(value)

    def take_string(self) -> str:
        length = self.take_u32()
        if length > _MAX_STRING_LENGTH:
            raise SnapshotError(
                f"implausible string length {length} at byte {self._offset}; "
                f"the file is probably corrupt"
            )
        if length == 0:
            return ""
        # strict errors: a decoding failure means the file is corrupt or the
        # writer changed encoding. Substituting replacement characters would
        # hide that and produce silently wrong symbol names.
        return self.take_bytes(length).decode("utf-8", errors="strict")


def _validate_header(cursor: _Cursor) -> tuple[int, int]:
    """Read and check the file header, returning (schema_version, file_count)."""
    magic = cursor.take_bytes(4)
    if magic != MAGIC:
        raise InvalidMagicError(magic)

    version = cursor.take_u32()
    if version != SUPPORTED_SCHEMA_VERSION:
        raise UnsupportedSchemaVersionError(version, SUPPORTED_SCHEMA_VERSION)

    return version, cursor.take_u32()


def read_snapshot_header(path: str | Path) -> tuple[int, int]:
    """Return ``(schema_version, file_count)`` without parsing the body.

    Cheap compatibility check before committing to loading a large snapshot.
    Raises the same errors as :func:`read_snapshot` for a malformed header.
    """
    data = Path(path).read_bytes()
    return _validate_header(_Cursor(data[:_HEADER_SIZE]))


def _read_node(cursor: _Cursor) -> AstNode:
    kind_value = cursor.take_u8()
    if kind_value > _MAX_KIND_VALUE:
        raise SnapshotError(
            f"unknown node kind {kind_value} at byte {cursor.offset}; "
            f"this build recognises 0 to {_MAX_KIND_VALUE}"
        )

    return AstNode(
        kind=AstNodeKind(kind_value),
        name=cursor.take_string(),
        byte_start=cursor.take_u32(),
        byte_end=cursor.take_u32(),
        first_child_index=cursor.take_u32(),
        child_count=cursor.take_u32(),
        parent_index=cursor.take_i32(),
    )


def read_snapshot(path: str | Path) -> list[SnapshotEntry]:
    """Load a snapshot written by the Sample Engine.

    Raises rather than returning ``None`` on failure, so a caller cannot
    accidentally proceed with a partial or misread file. An unrecognised schema
    version is rejected outright rather than parsed best-effort: reading a
    changed layout as though it were the current one succeeds and produces
    plausible-looking nonsense, which surfaces much later as inexplicable graph
    errors. Failing at load time is worth far more.

    Args:
        path: Path to the ``.raag.bin`` file.

    Returns:
        One entry per source file, in the order the Sample Engine wrote them.

    Raises:
        FileNotFoundError: The path does not exist.
        InvalidMagicError: The file is not a RAAG snapshot.
        UnsupportedSchemaVersionError: Written by an incompatible build.
        TruncatedSnapshotError: The file ends partway through a record.
        SnapshotError: Any other structural inconsistency.
    """
    data = Path(path).read_bytes()
    cursor = _Cursor(data)

    _, file_count = _validate_header(cursor)

    entries: list[SnapshotEntry] = []
    for _ in range(file_count):
        file_path = cursor.take_string()
        node_count = cursor.take_u32()

        nodes = [_read_node(cursor) for _ in range(node_count)]
        entries.append(SnapshotEntry(path=file_path, arena=AstArena(nodes=nodes)))

    return entries
