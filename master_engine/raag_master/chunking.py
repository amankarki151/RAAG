"""Splitting source files into retrievable chunks.

Chunk boundaries are taken from the AST rather than from line or token counts.
A fixed-window splitter will cut a function in half roughly whenever it feels
like it, and half a function retrieved as context is worse than no function: it
looks complete, reads as authoritative, and is missing the part that mattered.

A function or class is already the unit a developer reasons about, so it is the
unit worth retrieving.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from raag_tune.ast_types import AstArena, AstNodeKind, SnapshotEntry

__all__ = ["ChunkingConfig", "CodeChunk", "chunk_entry", "chunk_snapshot"]


@dataclass(frozen=True, slots=True)
class ChunkingConfig:
    """Bounds on what becomes a chunk.

    Both limits exist to protect retrieval quality rather than to save space.
    """

    min_bytes: int = 80
    """Below this, a chunk is noise.

    A one-line getter carries no information a retriever can use, and hundreds
    of them crowd out the handful of chunks that would have answered the query.
    """

    max_bytes: int = 8_000
    """Above this, a chunk is too diffuse to embed usefully.

    An embedding is a fixed-size summary. Compressing two thousand lines into
    the same vector width as twenty produces something that is weakly similar
    to everything and strongly similar to nothing.
    """

    include_classes: bool = True
    """Whether class declarations are chunked alongside their methods.

    A class body is retrieved as its own chunk only when it has no methods —
    otherwise its methods already cover the same bytes, and indexing both
    returns the same code twice for one query.
    """


@dataclass(frozen=True, slots=True)
class CodeChunk:
    """One retrievable unit of code.

    Attributes:
        chunk_id: Deterministic identifier derived from path, position, and
            content. Re-indexing an unchanged repository produces identical
            IDs, so an upsert overwrites rather than duplicating.
        path: Repository-relative path of the containing file.
        kind: ``"function"`` or ``"class"``.
        name: The declared name.
        qualified_name: ``Class.method`` where the parent is known, otherwise
            the bare name. What a developer would actually call this thing.
        parent_class: Enclosing class name, if any.
        text: The source text itself.
        byte_start: Offset of the first byte in the file.
        byte_end: Offset one past the last byte.
        line_start: 1-based first line, for citations a human can follow.
        line_end: 1-based last line.
    """

    chunk_id: str
    path: str
    kind: str
    name: str
    qualified_name: str
    parent_class: str | None
    text: str
    byte_start: int
    byte_end: int
    line_start: int
    line_end: int

    @property
    def byte_length(self) -> int:
        return self.byte_end - self.byte_start

    @property
    def line_count(self) -> int:
        return self.line_end - self.line_start + 1

    def embedding_text(self) -> str:
        """The text actually handed to the embedder.

        Prefixed with the location and qualified name. Two functions with
        identical bodies in different modules are different answers to a query,
        and without the prefix they embed to the same point — the retriever
        would then have no basis for preferring the relevant one.
        """
        header = f"{self.path} :: {self.qualified_name}"
        return f"{header}\n\n{self.text}"


def _line_offsets(source: str) -> list[int]:
    """Byte offset at which each line begins.

    Computed once per file and binary-searched per chunk. Counting newlines
    from the start for every chunk would be quadratic in the number of chunks,
    which is noticeable on a file with several hundred functions.
    """
    offsets = [0]
    for index, character in enumerate(source):
        if character == "\n":
            offsets.append(index + 1)
    return offsets


def _line_for_offset(line_offsets: list[int], offset: int) -> int:
    """1-based line number containing ``offset``."""
    low, high = 0, len(line_offsets) - 1
    while low < high:
        middle = (low + high + 1) // 2
        if line_offsets[middle] <= offset:
            low = middle
        else:
            high = middle - 1
    return low + 1


def _make_chunk_id(path: str, byte_start: int, text: str) -> str:
    """Stable identifier for a chunk.

    Includes the content hash, not just the position, so that editing a
    function changes its identity. A stale vector for code that no longer
    exists is worse than a missing one: it will be retrieved, read as current,
    and reasoned about.
    """
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    return f"{path}:{byte_start}:{digest}"


def _enclosing_class(arena: AstArena, index: int) -> str | None:
    """Name of the nearest enclosing class, walking up parent links."""
    current = arena[index].parent_index

    while current >= 0:
        node = arena[current]
        if node.kind is AstNodeKind.CLASS and node.name:
            # The Sample Engine encodes inheritance as "Derived : Base".
            return node.name.partition(" : ")[0].strip()
        current = node.parent_index

    return None


def _has_method(arena: AstArena, class_index: int) -> bool:
    """Whether a class declares any method, at any depth below it."""
    stack = list(arena.child_indices(class_index))

    while stack:
        index = stack.pop()
        node = arena[index]

        if node.kind is AstNodeKind.CLASS:
            continue  # A nested class's methods are not this class's.
        if node.kind is AstNodeKind.FUNCTION:
            return True

        stack.extend(arena.child_indices(index))

    return False


def chunk_entry(
    entry: SnapshotEntry,
    source: str,
    config: ChunkingConfig | None = None,
) -> list[CodeChunk]:
    """Split one parsed file into chunks.

    Args:
        entry: The file's parsed AST.
        source: The file's exact contents. Byte offsets in the AST index into
            this string, so passing a different revision silently yields chunks
            containing the wrong code.
        config: Size bounds and inclusion rules.

    Returns:
        Chunks in source order.
    """
    config = config or ChunkingConfig()
    arena = entry.arena

    if not arena.nodes or not source:
        return []

    source_bytes = source.encode("utf-8")
    line_offsets = _line_offsets(source)
    chunks: list[CodeChunk] = []

    for index, node in enumerate(arena.nodes):
        if node.kind is AstNodeKind.FUNCTION:
            kind = "function"
        elif node.kind is AstNodeKind.CLASS and config.include_classes:
            # Skip classes whose methods are already chunked individually —
            # indexing both returns the same bytes twice for one query.
            if _has_method(arena, index):
                continue
            kind = "class"
        else:
            continue

        if not node.name:
            # An unnamed declaration cannot be cited or referred to, and a
            # retrieved chunk nobody can locate is not useful context.
            continue

        start, end = node.byte_start, node.byte_end
        if end <= start or end > len(source_bytes):
            continue

        length = end - start
        if length < config.min_bytes or length > config.max_bytes:
            continue

        # Decoded rather than sliced as a string: the AST's offsets are byte
        # offsets, and slicing a str by them misplaces every boundary after
        # the first non-ASCII character.
        text = source_bytes[start:end].decode("utf-8", errors="replace")

        own_name = node.name.partition(" : ")[0].strip()
        parent_class = _enclosing_class(arena, index) if kind == "function" else None
        qualified = f"{parent_class}.{own_name}" if parent_class else own_name

        chunks.append(
            CodeChunk(
                chunk_id=_make_chunk_id(entry.path, start, text),
                path=entry.path,
                kind=kind,
                name=own_name,
                qualified_name=qualified,
                parent_class=parent_class,
                text=text,
                byte_start=start,
                byte_end=end,
                line_start=_line_for_offset(line_offsets, start),
                line_end=_line_for_offset(line_offsets, max(start, end - 1)),
            )
        )

    chunks.sort(key=lambda chunk: chunk.byte_start)
    return chunks


def chunk_snapshot(
    entries: list[SnapshotEntry],
    repo_root: Path,
    config: ChunkingConfig | None = None,
) -> tuple[list[CodeChunk], list[str]]:
    """Chunk every file in a snapshot.

    Source is read from disk because the snapshot stores structure, not text.
    That keeps snapshots small, at the cost of requiring the working tree to
    match the revision that was parsed.

    Args:
        entries: Parsed files from a snapshot.
        repo_root: Directory the snapshot's relative paths resolve against.
        config: Size bounds and inclusion rules.

    Returns:
        All chunks, and the paths that could not be read. Unreadable files are
        reported rather than raised on: one moved file should not abort an
        index over several hundred.
    """
    config = config or ChunkingConfig()
    chunks: list[CodeChunk] = []
    unreadable: list[str] = []

    for entry in entries:
        candidate = repo_root / entry.path
        try:
            source = candidate.read_text(encoding="utf-8", errors="replace")
        except (OSError, UnicodeDecodeError):
            unreadable.append(entry.path)
            continue

        chunks.extend(chunk_entry(entry, source, config))

    return chunks, unreadable
