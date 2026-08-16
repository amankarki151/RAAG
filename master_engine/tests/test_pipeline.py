"""Direct tests for the refactoring pipeline.

Existing coverage came almost entirely through CLI integration tests that
mostly exercise the early-exit paths (unknown target, missing snapshot).
These test run_refactor directly, so the actual orchestration logic — not
just its error handling — is exercised without depending on the CLI layer
above it.
"""

from __future__ import annotations

import networkx as nx

from raag_master.embeddings import HashEmbedder
from raag_master.indexer import index_snapshot
from raag_master.pipeline import run_refactor
from raag_master.reasoning import DryRunReasoner, ReasoningResult
from raag_master.vector_store import VectorStore
from raag_tune.ast_types import AstArena, AstNode, AstNodeKind, SnapshotEntry

DIMENSIONS = 32


def make_entry(path: str, name: str) -> SnapshotEntry:
    text_len = 200
    root = AstNode(
        kind=AstNodeKind.FILE,
        name="",
        byte_start=0,
        byte_end=text_len,
        first_child_index=1,
        child_count=1,
        parent_index=-1,
    )
    fn = AstNode(
        kind=AstNodeKind.FUNCTION,
        name=name,
        byte_start=0,
        byte_end=text_len,
        first_child_index=0,
        child_count=0,
        parent_index=0,
    )
    return SnapshotEntry(path=path, arena=AstArena(nodes=[root, fn]))


def make_graph() -> nx.DiGraph:
    graph = nx.DiGraph()
    graph.add_node("target.hpp", node_count=2, class_count=0, function_count=1)
    graph.add_node("consumer.cpp", node_count=2, class_count=0, function_count=1)
    graph.add_node("base.hpp", node_count=2, class_count=0, function_count=1)
    graph.add_edge("consumer.cpp", "target.hpp")
    graph.add_edge("target.hpp", "base.hpp")
    return graph


def indexed_store(tmp_path, repo_root) -> tuple[VectorStore, HashEmbedder]:
    embedder = HashEmbedder(dimensions=DIMENSIONS)
    store = VectorStore.in_memory("pipeline_test", DIMENSIONS)

    entries = [
        make_entry("target.hpp", "do_work"),
        make_entry("consumer.cpp", "call_it"),
    ]
    for entry in entries:
        (repo_root / entry.path).write_text(
            f"void {entry.arena[1].name}() {{ /* padded body for size floor */ }}\n"
        )

    index_snapshot(
        entries, make_graph(), store, embedder, repo_root, show_progress=False
    )
    return store, embedder


# --- Happy path ---------------------------------------------------------------


def test_run_refactor_returns_a_populated_outcome(tmp_path):
    store, embedder = indexed_store(tmp_path, tmp_path)

    outcome = run_refactor(
        "reduce coupling",
        "target.hpp",
        make_graph(),
        store,
        embedder,
        DryRunReasoner(),
        audit_path=None,
    )

    assert outcome.radius.target == "target.hpp"
    assert outcome.succeeded
    assert "target.hpp" in outcome.context.prompt


def test_run_refactor_scopes_retrieval_to_the_blast_radius(tmp_path):
    """The whole point of the pipeline: retrieval must not see the whole
    repository, only what the graph says the target can reach."""
    store, embedder = indexed_store(tmp_path, tmp_path)

    outcome = run_refactor(
        "anything",
        "target.hpp",
        make_graph(),
        store,
        embedder,
        DryRunReasoner(),
        audit_path=None,
    )

    retrieved_paths = {r.payload.path for r in outcome.results}
    assert retrieved_paths <= {"target.hpp", "consumer.cpp", "base.hpp"}


def test_run_refactor_includes_target_metrics_in_the_prompt(tmp_path):
    store, embedder = indexed_store(tmp_path, tmp_path)

    outcome = run_refactor(
        "anything",
        "target.hpp",
        make_graph(),
        store,
        embedder,
        DryRunReasoner(),
        audit_path=None,
    )

    # target.hpp: Ca=1 (consumer.cpp), Ce=1 (base.hpp)
    assert "Ca=1" in outcome.context.prompt
    assert "Ce=1" in outcome.context.prompt


def test_summary_lines_report_every_stage(tmp_path):
    store, embedder = indexed_store(tmp_path, tmp_path)

    outcome = run_refactor(
        "anything",
        "target.hpp",
        make_graph(),
        store,
        embedder,
        DryRunReasoner(),
        audit_path=None,
    )

    summary = "\n".join(outcome.summary_lines())
    assert "target.hpp" in summary
    assert "dry-run" in summary


# --- Audit integration ---------------------------------------------------------


def test_run_refactor_writes_an_audit_record_when_a_path_is_given(tmp_path):
    from raag_master.audit import AuditLog

    store, embedder = indexed_store(tmp_path, tmp_path)
    audit_path = tmp_path / "audit.db"

    outcome = run_refactor(
        "reduce coupling",
        "target.hpp",
        make_graph(),
        store,
        embedder,
        DryRunReasoner(),
        audit_path=audit_path,
    )

    assert outcome.audit_id is not None

    record = AuditLog(audit_path).get(outcome.audit_id)
    assert record is not None
    assert record.target == "target.hpp"
    assert record.request == "reduce coupling"


def test_run_refactor_skips_audit_when_path_is_none(tmp_path):
    store, embedder = indexed_store(tmp_path, tmp_path)

    outcome = run_refactor(
        "anything",
        "target.hpp",
        make_graph(),
        store,
        embedder,
        DryRunReasoner(),
        audit_path=None,
    )

    assert outcome.audit_id is None


def test_failed_reasoning_still_writes_an_audit_record(tmp_path):
    """A failed request is still a fact worth keeping — the audit write must
    happen whether or not the reasoning call succeeded."""
    from raag_master.audit import AuditLog

    class FailingReasoner:
        model = "failing-model"

        def reason(self, system_prompt: str, user_prompt: str) -> ReasoningResult:
            return ReasoningResult(text="", model=self.model, error="simulated failure")

    store, embedder = indexed_store(tmp_path, tmp_path)
    audit_path = tmp_path / "audit.db"

    outcome = run_refactor(
        "anything",
        "target.hpp",
        make_graph(),
        store,
        embedder,
        FailingReasoner(),
        audit_path=audit_path,
    )

    assert not outcome.succeeded
    assert outcome.audit_id is not None

    record = AuditLog(audit_path).get(outcome.audit_id)
    assert record is not None
    assert record.error == "simulated failure"
    assert record.response is None


# --- Parameters ------------------------------------------------------------


def test_depth_parameter_reaches_the_blast_radius_computation(tmp_path):
    store, embedder = indexed_store(tmp_path, tmp_path)

    shallow = run_refactor(
        "anything",
        "target.hpp",
        make_graph(),
        store,
        embedder,
        DryRunReasoner(),
        audit_path=None,
        depth=1,
    )

    assert shallow.radius.depth == 1


def test_unknown_target_raises_before_touching_retrieval(tmp_path):
    store, embedder = indexed_store(tmp_path, tmp_path)

    try:
        run_refactor(
            "anything",
            "does/not/exist.hpp",
            make_graph(),
            store,
            embedder,
            DryRunReasoner(),
            audit_path=None,
        )
        raise AssertionError("expected KeyError")
    except KeyError:
        pass
