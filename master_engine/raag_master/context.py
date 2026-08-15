"""Assembling the prompt sent to the reasoning model.

Retrieval decides *what* the model sees. This module decides how it sees it,
and the difference matters more than it sounds.

A pile of code chunks tells a model what the code says. The same chunks
labelled with their coupling metrics tell it what the code *costs to change* —
that a file has 335 dependents is not visible in its source, and it is the
single most important fact about whether a suggested refactor is reasonable.

The prompt is therefore built from three parts: the target with its metrics,
the retrieved context with each chunk's own metrics, and an explicit statement
of the structural constraints the model must respect.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from raag_master.blast_radius import BlastRadius
from raag_master.vector_store import SearchResult

__all__ = ["AssembledContext", "ContextBudget", "assemble_context"]


@dataclass(frozen=True, slots=True)
class ContextBudget:
    """Limits on how much context is assembled.

    Budgets are in characters rather than tokens. Character counts are exact
    and free to compute; token counts require the model's tokenizer and are
    approximate anyway across model versions. Four characters per token is the
    usual rough conversion, so the default here is roughly 30k tokens — well
    inside any modern context window, deliberately. A prompt that fills the
    window leaves no room for the model's own reasoning, and retrieval quality
    matters far more than retrieval quantity.
    """

    max_total_chars: int = 120_000
    max_chunk_chars: int = 6_000
    max_chunks: int = 25

    def __post_init__(self) -> None:
        if self.max_chunk_chars > self.max_total_chars:
            raise ValueError("max_chunk_chars cannot exceed max_total_chars")


@dataclass(slots=True)
class AssembledContext:
    """A finished prompt, plus what went into it.

    The included/excluded split is recorded rather than discarded so the audit
    log can answer "why did the model say that" later. A suggestion made
    without seeing a critical file is not wrong so much as uninformed, and
    telling those apart afterwards requires knowing what was in the prompt.
    """

    prompt: str
    included_chunks: list[str] = field(default_factory=list)
    excluded_chunks: list[str] = field(default_factory=list)
    total_chars: int = 0
    budget_exhausted: bool = False

    @property
    def included_count(self) -> int:
        return len(self.included_chunks)

    @property
    def excluded_count(self) -> int:
        return len(self.excluded_chunks)


SYSTEM_PROMPT = """\
You are an architectural reviewer for a large codebase. You are given a target \
file, the files that depend on it, and code retrieved from within that \
dependency radius. Each file carries measured coupling metrics.

Metric meanings:
- Ca (afferent coupling): how many files depend on this one. High Ca means a \
change here is expensive — every dependent is a place that can break.
- Ce (efferent coupling): how many files this one depends on. High Ce means \
fragility to upstream change.
- I (instability) = Ce / (Ca + Ce), from 0.0 to 1.0. Low I means stable and \
depended-upon; high I means it depends outward and little depends on it.

Rules you must follow:
1. Ground every claim in code you were actually shown. If answering properly \
requires a file you were not given, say so explicitly rather than guessing at \
its contents.
2. Weigh suggestions by measured impact. A refactor touching a file with 300 \
dependents needs far stronger justification than one touching a file with 2.
3. A high instability score is not automatically a defect. Adapters, plugins, \
and entry points are supposed to be unstable — that is their role.
4. Prefer changes that reduce coupling without breaking dependents over \
changes that are locally elegant but ripple outward.
5. Name specific files and functions. "Consider extracting an interface" is \
not actionable; "extract IFoo from foo.hpp so bar.cpp and baz.cpp depend on \
the interface rather than the implementation" is.
"""


def _format_metrics(afferent: int, efferent: int, instability: float) -> str:
    return f"Ca={afferent} Ce={efferent} I={instability:.2f}"


def _format_chunk(result: SearchResult, index: int) -> str:
    payload = result.payload
    metrics = _format_metrics(
        payload.afferent_coupling, payload.efferent_coupling, payload.instability
    )
    return (
        f"--- [{index}] {payload.path}:{payload.line_start}-{payload.line_end}\n"
        f"    {payload.qualified_name}  ({metrics})  "
        f"relevance={result.score:.3f}\n"
        f"```\n{payload.text}\n```\n"
    )


def assemble_context(
    request: str,
    radius: BlastRadius,
    results: list[SearchResult],
    *,
    target_metrics: tuple[int, int, float] | None = None,
    budget: ContextBudget | None = None,
) -> AssembledContext:
    """Build the prompt for a refactoring request.

    Chunks are added in relevance order until the budget runs out. Ordering by
    relevance rather than by file means that when the budget binds, what
    survives is what the retriever judged most useful — not whatever happened
    to sort first alphabetically.

    Args:
        request: What the user actually asked for.
        radius: The computed blast radius, for impact framing.
        results: Retrieved chunks, most relevant first.
        target_metrics: The target file's own (Ca, Ce, I), stated separately
            because the target's cost of change frames everything else.
        budget: Size limits.

    Returns:
        The assembled prompt and an account of what was included or dropped.
    """
    budget = budget or ContextBudget()

    sections: list[str] = []

    sections.append(f"# Request\n\n{request.strip()}\n")

    target_line = f"`{radius.target}`"
    if target_metrics is not None:
        target_line += f"  ({_format_metrics(*target_metrics)})"

    sections.append(
        f"# Target\n\n{target_line}\n\nBlast radius: {radius.summary_line()}\n"
    )

    if radius.dependents:
        listing = "\n".join(f"- {path}" for path in radius.dependents[:20])
        more = len(radius.dependents) - 20
        suffix = f"\n- ... and {more} more" if more > 0 else ""
        sections.append(
            f"# Files that depend on the target ({len(radius.dependents)})\n\n"
            f"These break if the target's interface changes.\n\n{listing}{suffix}\n"
        )
    else:
        sections.append(
            "# Files that depend on the target\n\n"
            "None. Nothing in this repository depends on the target, so a "
            "change here has no internal blast radius.\n"
        )

    if radius.truncated:
        sections.append(
            "# Note on completeness\n\n"
            f"The blast radius was truncated: {radius.total_reachable} files were "
            f"reachable and only the nearest were included. Treat the impact "
            f"figures as a lower bound.\n"
        )

    header = "\n".join(sections)
    used = len(header) + len(SYSTEM_PROMPT)

    included: list[str] = []
    excluded: list[str] = []
    chunk_texts: list[str] = []
    exhausted = False

    for index, result in enumerate(results, start=1):
        if len(included) >= budget.max_chunks:
            excluded.append(result.payload.qualified_name)
            exhausted = True
            continue

        formatted = _format_chunk(result, index)

        if len(formatted) > budget.max_chunk_chars:
            # Truncated rather than dropped: a long function's signature and
            # opening lines are usually the part that answers a structural
            # question, and losing the whole chunk loses that too.
            keep = budget.max_chunk_chars
            formatted = formatted[:keep] + "\n... [chunk truncated]\n```\n"

        if used + len(formatted) > budget.max_total_chars:
            excluded.append(result.payload.qualified_name)
            exhausted = True
            continue

        chunk_texts.append(formatted)
        included.append(result.payload.qualified_name)
        used += len(formatted)

    if chunk_texts:
        body = "\n".join(chunk_texts)
        sections.append(
            f"# Retrieved code ({len(included)} chunks, "
            f"from within the blast radius)\n\n"
            f"{body}"
        )
    else:
        sections.append(
            "# Retrieved code\n\n"
            "No code was retrieved within the blast radius. Say so rather than "
            "reasoning about code you cannot see.\n"
        )

    prompt = "\n".join(sections)

    return AssembledContext(
        prompt=prompt,
        included_chunks=included,
        excluded_chunks=excluded,
        total_chars=len(prompt),
        budget_exhausted=exhausted,
    )
