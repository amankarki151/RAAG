"""Tests for the audit log.

The log's purpose is reconstruction: months later, explaining why the system
said what it said. These tests assert that everything needed for that survives
a round trip, including the cases where the request failed.
"""

from __future__ import annotations

from raag_master.audit import AuditLog, AuditRecord


def record(
    target: str = "core.hpp",
    request: str = "reduce coupling",
    *,
    response: str | None = "Extract an interface.",
    error: str | None = None,
) -> AuditRecord:
    return AuditRecord(
        target=target,
        request=request,
        model="test-model",
        dependents=["a.cpp", "b.cpp"],
        dependencies=["util.hpp"],
        radius_depth=2,
        radius_truncated=False,
        target_afferent=12,
        target_efferent=3,
        target_instability=0.2,
        retrieved_chunks=["fn_one", "fn_two"],
        excluded_chunks=["fn_dropped"],
        prompt="the full assembled prompt",
        response=response,
        input_tokens=1_500,
        output_tokens=400,
        duration_ms=2_100,
        error=error,
    )


# --- Round trip --------------------------------------------------------------


def test_record_returns_an_id(tmp_path):
    log = AuditLog(tmp_path / "audit.db")

    assert log.record(record()) > 0


def test_every_field_survives_a_round_trip(tmp_path):
    log = AuditLog(tmp_path / "audit.db")
    record_id = log.record(record())

    stored = log.get(record_id)

    assert stored is not None
    assert stored.target == "core.hpp"
    assert stored.request == "reduce coupling"
    assert stored.model == "test-model"
    assert stored.dependents == ["a.cpp", "b.cpp"]
    assert stored.dependencies == ["util.hpp"]
    assert stored.retrieved_chunks == ["fn_one", "fn_two"]
    assert stored.excluded_chunks == ["fn_dropped"]
    assert stored.prompt == "the full assembled prompt"
    assert stored.response == "Extract an interface."
    assert stored.input_tokens == 1_500
    assert stored.target_instability == 0.2


def test_the_full_prompt_is_stored_verbatim(tmp_path):
    """Redundant with the structured columns, deliberately. The columns make
    the log queryable; the raw prompt makes it reproducible. Storing only the
    summary means a future change to prompt assembly invalidates history."""
    log = AuditLog(tmp_path / "audit.db")
    prompt = "# Request\n\nfull text\n\n# Target\n\n`core.hpp` (Ca=12 Ce=3 I=0.20)"

    entry = record()
    entry.prompt = prompt
    stored = log.get(log.record(entry))

    assert stored is not None
    assert stored.prompt == prompt


def test_created_at_is_set_automatically(tmp_path):
    log = AuditLog(tmp_path / "audit.db")

    stored = log.get(log.record(record()))

    assert stored is not None
    assert stored.created_at


def test_missing_record_returns_none(tmp_path):
    log = AuditLog(tmp_path / "audit.db")

    assert log.get(9_999) is None


# --- Failures ----------------------------------------------------------------


def test_failed_requests_are_logged_too(tmp_path):
    """A run that failed is itself a fact worth keeping: it records that a
    question was asked and went unanswered."""
    log = AuditLog(tmp_path / "audit.db")

    record_id = log.record(record(response=None, error="APIConnectionError: timed out"))
    stored = log.get(record_id)

    assert stored is not None
    assert stored.error is not None
    assert stored.response is None


def test_failures_can_be_listed_separately(tmp_path):
    log = AuditLog(tmp_path / "audit.db")
    log.record(record())
    log.record(record(error="boom"))
    log.record(record())

    failures = log.failures()

    assert len(failures) == 1
    assert failures[0].error == "boom"


# --- Querying ----------------------------------------------------------------


def test_recent_returns_newest_first(tmp_path):
    log = AuditLog(tmp_path / "audit.db")
    log.record(record(request="first"))
    log.record(record(request="second"))

    assert log.recent()[0].request == "second"


def test_recent_respects_the_limit(tmp_path):
    log = AuditLog(tmp_path / "audit.db")
    for i in range(10):
        log.record(record(request=f"request {i}"))

    assert len(log.recent(limit=3)) == 3


def test_for_target_filters_by_file(tmp_path):
    """The query the log mainly exists to answer: before changing a file, what
    has the system already said about it."""
    log = AuditLog(tmp_path / "audit.db")
    log.record(record(target="a.hpp"))
    log.record(record(target="b.hpp"))
    log.record(record(target="a.hpp"))

    assert len(log.for_target("a.hpp")) == 2
    assert len(log.for_target("b.hpp")) == 1


def test_for_target_on_unknown_file_is_empty(tmp_path):
    log = AuditLog(tmp_path / "audit.db")
    log.record(record(target="a.hpp"))

    assert log.for_target("never/seen.hpp") == []


def test_count_reflects_every_record(tmp_path):
    log = AuditLog(tmp_path / "audit.db")
    for _ in range(5):
        log.record(record())

    assert log.count() == 5


# --- Persistence -------------------------------------------------------------


def test_the_log_persists_across_instances(tmp_path):
    path = tmp_path / "audit.db"

    AuditLog(path).record(record())

    assert AuditLog(path).count() == 1


def test_schema_creation_is_idempotent(tmp_path):
    path = tmp_path / "audit.db"

    AuditLog(path)
    AuditLog(path)
    log = AuditLog(path)

    assert log.count() == 0


def test_parent_directories_are_created(tmp_path):
    log = AuditLog(tmp_path / "nested" / "deeper" / "audit.db")

    log.record(record())

    assert log.path.exists()
