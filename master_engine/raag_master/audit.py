"""Audit logging for every reasoning request.

A refactoring suggestion nobody can explain afterwards cannot be trusted with
production architecture. This log exists so that any suggestion the system
made can be reconstructed months later: what was asked, what the graph said
was affected, which code was retrieved, what exact prompt was built, and what
came back.

SQLite rather than a log file because these records get queried, not just read
— "show me every suggestion that touched this file" is the question that
matters, and grep answers it badly.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

__all__ = ["AuditLog", "AuditRecord"]

_SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS requests (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at        TEXT    NOT NULL,
    target            TEXT    NOT NULL,
    request           TEXT    NOT NULL,
    model             TEXT    NOT NULL,
    dependents        TEXT    NOT NULL,
    dependencies      TEXT    NOT NULL,
    radius_depth      INTEGER NOT NULL,
    radius_truncated  INTEGER NOT NULL,
    target_afferent   INTEGER NOT NULL,
    target_efferent   INTEGER NOT NULL,
    target_instability REAL   NOT NULL,
    retrieved_chunks  TEXT    NOT NULL,
    excluded_chunks   TEXT    NOT NULL,
    prompt            TEXT    NOT NULL,
    prompt_chars      INTEGER NOT NULL,
    response          TEXT,
    input_tokens      INTEGER,
    output_tokens     INTEGER,
    duration_ms       INTEGER,
    error             TEXT
);

CREATE INDEX IF NOT EXISTS idx_requests_target ON requests(target);
CREATE INDEX IF NOT EXISTS idx_requests_created ON requests(created_at);
"""


@dataclass(slots=True)
class AuditRecord:
    """One logged reasoning request.

    Every field the model saw is recorded, including the full prompt. That is
    deliberately redundant with the structured columns: the columns make the
    log queryable, and the raw prompt makes it reproducible. Storing only the
    structured summary would mean a future change to prompt assembly silently
    invalidates every historical record.
    """

    target: str
    request: str
    model: str
    dependents: list[str]
    dependencies: list[str]
    radius_depth: int
    radius_truncated: bool
    target_afferent: int
    target_efferent: int
    target_instability: float
    retrieved_chunks: list[str]
    excluded_chunks: list[str]
    prompt: str
    response: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    duration_ms: int | None = None
    error: str | None = None
    created_at: str = ""
    record_id: int | None = None

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = datetime.now(UTC).isoformat()


class AuditLog:
    """SQLite-backed audit log.

    Connections are opened per operation rather than held, so the log is safe
    to use from a CLI that runs once and exits, and from tests that create and
    discard databases freely.
    """

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)
        self._ensure_schema()

    @property
    def path(self) -> Path:
        return self._path

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self._path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _ensure_schema(self) -> None:
        with self._connect() as connection:
            connection.executescript(_SCHEMA)
            existing = connection.execute(
                "SELECT version FROM schema_version LIMIT 1"
            ).fetchone()
            if existing is None:
                connection.execute(
                    "INSERT INTO schema_version (version) VALUES (?)",
                    (_SCHEMA_VERSION,),
                )

    def record(self, entry: AuditRecord) -> int:
        """Write a record and return its id."""
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO requests (
                    created_at, target, request, model,
                    dependents, dependencies, radius_depth, radius_truncated,
                    target_afferent, target_efferent, target_instability,
                    retrieved_chunks, excluded_chunks, prompt, prompt_chars,
                    response, input_tokens, output_tokens, duration_ms, error
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    entry.created_at,
                    entry.target,
                    entry.request,
                    entry.model,
                    json.dumps(entry.dependents),
                    json.dumps(entry.dependencies),
                    entry.radius_depth,
                    int(entry.radius_truncated),
                    entry.target_afferent,
                    entry.target_efferent,
                    entry.target_instability,
                    json.dumps(entry.retrieved_chunks),
                    json.dumps(entry.excluded_chunks),
                    entry.prompt,
                    len(entry.prompt),
                    entry.response,
                    entry.input_tokens,
                    entry.output_tokens,
                    entry.duration_ms,
                    entry.error,
                ),
            )
            entry.record_id = int(cursor.lastrowid or 0)
            return entry.record_id

    def get(self, record_id: int) -> AuditRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM requests WHERE id = ?", (record_id,)
            ).fetchone()

        return _row_to_record(row) if row else None

    def recent(self, limit: int = 20) -> list[AuditRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM requests ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()

        return [_row_to_record(row) for row in rows]

    def for_target(self, target: str, limit: int = 20) -> list[AuditRecord]:
        """Every request made about one file.

        The query the log mainly exists to answer: before changing a file,
        what has the system already said about it.
        """
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM requests WHERE target = ? ORDER BY id DESC LIMIT ?",
                (target, limit),
            ).fetchall()

        return [_row_to_record(row) for row in rows]

    def count(self) -> int:
        with self._connect() as connection:
            row = connection.execute("SELECT COUNT(*) AS n FROM requests").fetchone()
        return int(row["n"])

    def failures(self, limit: int = 20) -> list[AuditRecord]:
        """Requests that errored. Logged rather than dropped, because a run
        that failed is itself a fact worth keeping."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM requests WHERE error IS NOT NULL "
                "ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()

        return [_row_to_record(row) for row in rows]


def _row_to_record(row: sqlite3.Row) -> AuditRecord:
    return AuditRecord(
        record_id=row["id"],
        created_at=row["created_at"],
        target=row["target"],
        request=row["request"],
        model=row["model"],
        dependents=json.loads(row["dependents"]),
        dependencies=json.loads(row["dependencies"]),
        radius_depth=row["radius_depth"],
        radius_truncated=bool(row["radius_truncated"]),
        target_afferent=row["target_afferent"],
        target_efferent=row["target_efferent"],
        target_instability=row["target_instability"],
        retrieved_chunks=json.loads(row["retrieved_chunks"]),
        excluded_chunks=json.loads(row["excluded_chunks"]),
        prompt=row["prompt"],
        response=row["response"],
        input_tokens=row["input_tokens"],
        output_tokens=row["output_tokens"],
        duration_ms=row["duration_ms"],
        error=row["error"],
    )
