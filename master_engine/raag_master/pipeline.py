"""The complete refactoring pipeline.

Six stages, each already tested independently:

    request
       |
       v
    blast radius        graph traversal from the target
       |
       v
    scoped retrieval    vector search restricted to that radius
       |
       v
    context assembly    prompt built with metrics injected
       |
       v
    reasoning           one API call
       |
       v
    audit               everything recorded, success or failure

This module exists to wire them together and to guarantee the audit record is
written whatever happens. Nothing here does analysis of its own.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import networkx as nx

from raag_master.audit import AuditLog, AuditRecord
from raag_master.blast_radius import BlastRadius, compute_blast_radius
from raag_master.context import (
    SYSTEM_PROMPT,
    AssembledContext,
    ContextBudget,
    assemble_context,
)
from raag_master.embeddings import Embedder
from raag_master.indexer import search_code
from raag_master.reasoning import Reasoner, ReasoningResult
from raag_master.vector_store import SearchResult, VectorStore
from raag_tune.metrics import compute_module_metrics

__all__ = ["RefactorOutcome", "run_refactor"]


@dataclass(slots=True)
class RefactorOutcome:
    """Everything one refactor request produced."""

    radius: BlastRadius
    results: list[SearchResult]
    context: AssembledContext
    reasoning: ReasoningResult
    audit_id: int | None = None

    @property
    def succeeded(self) -> bool:
        return self.reasoning.succeeded

    def summary_lines(self) -> list[str]:
        lines = [
            f"Target          {self.radius.target}",
            f"Blast radius    {self.radius.summary_line()}",
            f"Retrieved       {len(self.results)} chunks, "
            f"{self.context.included_count} included in prompt",
            f"Prompt size     {self.context.total_chars:,} characters",
            f"Model           {self.reasoning.model}",
        ]

        if self.reasoning.input_tokens:
            lines.append(
                f"Tokens          {self.reasoning.input_tokens:,} in, "
                f"{self.reasoning.output_tokens:,} out"
            )
        if self.reasoning.duration_ms:
            lines.append(f"Duration        {self.reasoning.duration_ms:,} ms")
        if self.audit_id is not None:
            lines.append(f"Audit record    #{self.audit_id}")
        if self.context.excluded_count:
            lines.append(
                f"Dropped         {self.context.excluded_count} chunks "
                f"(budget exhausted)"
            )

        return lines


def run_refactor(
    request: str,
    target: str,
    graph: nx.DiGraph,
    store: VectorStore,
    embedder: Embedder,
    reasoner: Reasoner,
    *,
    audit_path: Path | str | None = None,
    depth: int = 2,
    retrieval_limit: int = 15,
    budget: ContextBudget | None = None,
) -> RefactorOutcome:
    """Run one refactoring request end to end.

    Args:
        request: What the user asked for, in their own words.
        target: File the request is about. Must exist in the graph.
        graph: Dependency graph from the Tune Engine.
        store: Vector store holding indexed chunks.
        embedder: Must match the one the store was indexed with.
        reasoner: Backend. Pass DryRunReasoner to assemble without calling out.
        audit_path: Where to log. None disables logging, which is appropriate
            for tests and nothing else.
        depth: Blast radius traversal depth.
        retrieval_limit: Chunks to retrieve before budget filtering.
        budget: Context size limits.

    Returns:
        The outcome, including a failed reasoning result if the call errored.
        Failure is returned rather than raised so the audit record is still
        written — an unanswered question is a fact worth keeping.

    Raises:
        KeyError: The target is not in the graph.
    """
    radius = compute_blast_radius(graph, target, depth=depth)

    target_metrics_obj = compute_module_metrics(graph, target)
    target_metrics = (
        target_metrics_obj.afferent_coupling,
        target_metrics_obj.efferent_coupling,
        target_metrics_obj.instability,
    )

    # The whole point of the system: retrieval is restricted to files the
    # graph says a change can reach. Semantic similarity ranks within that
    # set; it does not choose the set.
    results = search_code(
        request,
        store,
        embedder,
        limit=retrieval_limit,
        paths=radius.all_paths,
    )

    context = assemble_context(
        request,
        radius,
        results,
        target_metrics=target_metrics,
        budget=budget,
    )

    reasoning = reasoner.reason(SYSTEM_PROMPT, context.prompt)

    audit_id: int | None = None
    if audit_path is not None:
        log = AuditLog(audit_path)
        audit_id = log.record(
            AuditRecord(
                target=target,
                request=request,
                model=reasoning.model,
                dependents=radius.dependents,
                dependencies=radius.dependencies,
                radius_depth=radius.depth,
                radius_truncated=radius.truncated,
                target_afferent=target_metrics[0],
                target_efferent=target_metrics[1],
                target_instability=target_metrics[2],
                retrieved_chunks=context.included_chunks,
                excluded_chunks=context.excluded_chunks,
                prompt=context.prompt,
                response=reasoning.text or None,
                input_tokens=reasoning.input_tokens or None,
                output_tokens=reasoning.output_tokens or None,
                duration_ms=reasoning.duration_ms or None,
                error=reasoning.error,
            )
        )

    return RefactorOutcome(
        radius=radius,
        results=results,
        context=context,
        reasoning=reasoning,
        audit_id=audit_id,
    )
