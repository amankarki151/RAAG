"""Tests for prompt assembly.

Prompt assembly is the least visible part of the pipeline and the easiest to
get quietly wrong: a prompt missing its metrics still looks like a prompt, and
the model still answers. These tests assert the specific things whose absence
would silently degrade output quality.
"""

from __future__ import annotations

import pytest

from raag_master.blast_radius import BlastRadius
from raag_master.context import ContextBudget, assemble_context
from raag_master.vector_store import ChunkPayload, SearchResult


def result(
    path: str,
    name: str,
    text: str,
    *,
    score: float = 0.8,
    afferent: int = 5,
    efferent: int = 2,
    instability: float = 0.29,
) -> SearchResult:
    return SearchResult(
        score=score,
        payload=ChunkPayload(
            path=path,
            kind="function",
            name=name,
            qualified_name=name,
            parent_class=None,
            text=text,
            line_start=10,
            line_end=10 + text.count("\n"),
            afferent_coupling=afferent,
            efferent_coupling=efferent,
            instability=instability,
        ),
    )


def radius(
    target: str = "core.hpp",
    dependents: list[str] | None = None,
    dependencies: list[str] | None = None,
    truncated: bool = False,
    total: int = 0,
) -> BlastRadius:
    return BlastRadius(
        target=target,
        dependents=dependents if dependents is not None else ["a.cpp", "b.cpp"],
        dependencies=dependencies if dependencies is not None else ["util.hpp"],
        depth=2,
        truncated=truncated,
        total_reachable=total,
    )


# --- Content that must be present --------------------------------------------


def test_prompt_contains_the_request():
    context = assemble_context("split this class in two", radius(), [])

    assert "split this class in two" in context.prompt


def test_prompt_names_the_target():
    context = assemble_context("anything", radius(target="core.hpp"), [])

    assert "core.hpp" in context.prompt


def test_target_metrics_are_stated():
    """The target's cost of change frames every suggestion. Without it the
    model has no basis for weighing a risky refactor against a safe one."""
    context = assemble_context(
        "reduce coupling", radius(), [], target_metrics=(335, 32, 0.09)
    )

    assert "Ca=335" in context.prompt
    assert "Ce=32" in context.prompt
    assert "I=0.09" in context.prompt


def test_dependents_are_listed_as_what_breaks():
    context = assemble_context(
        "change the interface",
        radius(dependents=["consumer_a.cpp", "consumer_b.cpp"]),
        [],
    )

    assert "consumer_a.cpp" in context.prompt
    assert "consumer_b.cpp" in context.prompt
    assert "break" in context.prompt.lower()


def test_each_chunk_carries_its_own_metrics():
    """Per-chunk metrics are what let the model weigh one file against another,
    rather than treating all retrieved code as equally safe to change."""
    context = assemble_context(
        "anything",
        radius(),
        [
            result(
                "hot.hpp",
                "f",
                "def f(): pass",
                afferent=200,
                efferent=1,
                instability=0.005,
            )
        ],
    )

    assert "Ca=200" in context.prompt


def test_chunks_include_file_and_line_citations():
    context = assemble_context(
        "anything", radius(), [result("src/thing.hpp", "do_thing", "body")]
    )

    assert "src/thing.hpp:10" in context.prompt


def test_relevance_scores_are_shown():
    context = assemble_context(
        "anything", radius(), [result("a.hpp", "f", "body", score=0.912)]
    )

    assert "0.912" in context.prompt


# --- Honest reporting of gaps ------------------------------------------------


def test_no_dependents_is_stated_explicitly():
    """Silence would read as 'unknown'. An explicit 'nothing depends on this'
    is a fact the model should use."""
    context = assemble_context("anything", radius(dependents=[]), [])

    assert "None." in context.prompt


def test_no_retrieved_code_is_stated_explicitly():
    context = assemble_context("anything", radius(), [])

    assert "No code was retrieved" in context.prompt


def test_truncated_radius_is_flagged_in_the_prompt():
    """The model must know its impact figures are a lower bound, or it will
    reason as though it has seen the whole picture."""
    context = assemble_context("anything", radius(truncated=True, total=250), [])

    assert "truncated" in context.prompt.lower()
    assert "lower bound" in context.prompt.lower()


# --- Budget ------------------------------------------------------------------


def test_chunks_are_included_until_the_budget_runs_out():
    chunks = [result(f"f{i}.hpp", f"fn{i}", "x" * 500) for i in range(20)]

    context = assemble_context(
        "anything",
        radius(),
        chunks,
        budget=ContextBudget(max_total_chars=4_000, max_chunk_chars=1_000),
    )

    assert context.included_count < 20
    assert context.budget_exhausted


def test_chunk_count_cap_is_respected():
    chunks = [result(f"f{i}.hpp", f"fn{i}", "body") for i in range(30)]

    context = assemble_context(
        "anything", radius(), chunks, budget=ContextBudget(max_chunks=5)
    )

    assert context.included_count == 5
    assert context.excluded_count == 25


def test_relevance_order_is_preserved_under_the_budget():
    """When the budget binds, what survives should be what the retriever
    judged most useful — not an arbitrary slice."""
    chunks = [
        result("first.hpp", "most_relevant", "body", score=0.99),
        result("second.hpp", "less_relevant", "body", score=0.50),
    ]

    context = assemble_context(
        "anything", radius(), chunks, budget=ContextBudget(max_chunks=1)
    )

    assert context.included_chunks == ["most_relevant"]


def test_oversized_chunk_is_truncated_not_dropped():
    """A long function's signature and opening lines usually answer the
    structural question; dropping the whole chunk loses that too."""
    chunks = [result("big.hpp", "huge_fn", "x" * 50_000)]

    context = assemble_context(
        "anything", radius(), chunks, budget=ContextBudget(max_chunk_chars=1_000)
    )

    assert context.included_count == 1
    assert "chunk truncated" in context.prompt


def test_excluded_chunks_are_recorded_not_silently_dropped():
    """The audit log needs to distinguish 'the model was wrong' from 'the
    model never saw the relevant file'."""
    chunks = [result(f"f{i}.hpp", f"fn{i}", "body") for i in range(10)]

    context = assemble_context(
        "anything", radius(), chunks, budget=ContextBudget(max_chunks=3)
    )

    assert context.excluded_chunks == [f"fn{i}" for i in range(3, 10)]


def test_budget_rejects_impossible_configuration():
    with pytest.raises(ValueError):
        ContextBudget(max_total_chars=100, max_chunk_chars=1_000)


def test_total_chars_matches_the_prompt():
    context = assemble_context("anything", radius(), [result("a.hpp", "f", "body")])

    assert context.total_chars == len(context.prompt)
