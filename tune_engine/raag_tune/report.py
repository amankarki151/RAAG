"""Assembly of metrics into a report, and evaluation against thresholds.

Metrics are numbers. A report is a judgement about them, and judgement needs
policy — which is why thresholds live here as explicit, configurable data rather
than as constants buried in the metric functions.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import networkx as nx

from raag_tune.ast_types import SnapshotEntry
from raag_tune.cohesion import ClassCohesion, compute_cohesion
from raag_tune.dependency_graph import GraphSummary, summarise_graph
from raag_tune.edge_extractor import ExtractionReport
from raag_tune.metrics import ModuleMetrics, compute_all_metrics

__all__ = ["MetricsReport", "Severity", "Thresholds", "Violation", "build_report"]

from enum import StrEnum


class Severity(StrEnum):
    """How strongly a violation should be acted on."""

    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class Thresholds:
    """Policy for what counts as a violation.

    Defaults are starting points, not universal truths. Every threshold here is
    a judgement about a particular codebase's conventions, and a tool that
    hard-codes them will be wrong about half the repositories it sees.

    A note on ``max_instability``: a high instability figure is not by itself a
    defect. An adapter or plugin layer *should* be unstable — depending outward
    and being depended on by nothing is exactly its job. What matters is a
    module that is both depended upon and unstable, which is why the check is
    gated on ``min_afferent_for_instability`` rather than firing on instability
    alone. Martin's own model resolves this properly by pairing instability with
    abstractness; that pairing is on the roadmap and would replace this gate.
    """

    max_instability: float = 0.4
    """Instability above which a depended-upon module is flagged."""

    min_afferent_for_instability: int = 3
    """Afferent coupling below which instability is not judged at all."""

    max_efferent_coupling: int = 20
    """Outgoing dependency count above which a module is flagged as fragile."""

    max_lcom4: int = 1
    """Connected method components above which a class is flagged."""

    min_methods_for_cohesion: int = 3
    """Method count below which cohesion is not judged."""

    fail_on_cycles: bool = True
    """Whether a dependency cycle counts as an error rather than a warning."""


@dataclass(frozen=True, slots=True)
class Violation:
    """A single threshold breach.

    Carries the measured value and the threshold it crossed. A violation that
    only says "too high" gives a reader no way to judge how far off they are or
    whether the threshold is reasonable for their codebase.
    """

    rule: str
    severity: Severity
    subject: str
    message: str
    measured: float
    threshold: float

    def format_line(self) -> str:
        return (
            f"[{self.severity.upper():<7}] {self.rule:<22} {self.subject}\n"
            f"           {self.message}"
        )


@dataclass(slots=True)
class MetricsReport:
    """Everything the Tune Engine knows about one repository."""

    graph_summary: GraphSummary
    modules: list[ModuleMetrics] = field(default_factory=list)
    classes: list[ClassCohesion] = field(default_factory=list)
    violations: list[Violation] = field(default_factory=list)
    cycles: list[list[str]] = field(default_factory=list)
    resolution_summary: str = ""
    thresholds: Thresholds = field(default_factory=Thresholds)

    @property
    def error_count(self) -> int:
        return sum(1 for v in self.violations if v.severity is Severity.ERROR)

    @property
    def warning_count(self) -> int:
        return sum(1 for v in self.violations if v.severity is Severity.WARNING)

    @property
    def passed(self) -> bool:
        """Whether the repository clears its thresholds.

        Warnings do not fail a run. A gate that blocks on every soft signal
        gets disabled, and a disabled gate protects nothing.
        """
        return self.error_count == 0

    def most_unstable(self, limit: int = 10) -> list[ModuleMetrics]:
        """Depended-upon modules with the highest instability.

        Filtered to modules something actually depends on, because an unstable
        leaf is uninteresting — nothing breaks when it changes.
        """
        candidates = [m for m in self.modules if m.afferent_coupling > 0]
        return sorted(candidates, key=lambda m: (-m.instability, -m.afferent_coupling))[
            :limit
        ]

    def most_depended_upon(self, limit: int = 10) -> list[ModuleMetrics]:
        return sorted(self.modules, key=lambda m: -m.afferent_coupling)[:limit]

    def least_cohesive(self, limit: int = 10) -> list[ClassCohesion]:
        return [c for c in self.classes if not c.is_cohesive][:limit]

    def to_dict(self) -> dict[str, object]:
        return {
            "summary": asdict(self.graph_summary),
            "thresholds": asdict(self.thresholds),
            "resolution": self.resolution_summary,
            "modules": [asdict(m) for m in self.modules],
            "classes": [asdict(c) for c in self.classes],
            "violations": [asdict(v) for v in self.violations],
            "cycles": self.cycles,
        }

    def write_json(self, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(self.to_dict(), indent=2, sort_keys=True), encoding="utf-8"
        )


def _check_instability(
    metrics: list[ModuleMetrics], thresholds: Thresholds
) -> list[Violation]:
    violations: list[Violation] = []

    for module in metrics:
        if module.afferent_coupling < thresholds.min_afferent_for_instability:
            continue
        if module.instability <= thresholds.max_instability:
            continue

        violations.append(
            Violation(
                rule="instability",
                severity=Severity.ERROR,
                subject=module.path,
                message=(
                    f"{module.afferent_coupling} modules depend on this file, but it "
                    f"depends on {module.efferent_coupling} others "
                    f"(I={module.instability:.2f}, "
                    f"limit {thresholds.max_instability:.2f}). "
                    f"Changes upstream can propagate through it to everything below."
                ),
                measured=module.instability,
                threshold=thresholds.max_instability,
            )
        )

    return violations


def _check_efferent(
    metrics: list[ModuleMetrics], thresholds: Thresholds
) -> list[Violation]:
    return [
        Violation(
            rule="efferent-coupling",
            severity=Severity.WARNING,
            subject=module.path,
            message=(
                f"Depends on {module.efferent_coupling} other files "
                f"(limit {thresholds.max_efferent_coupling}). "
                f"Each is a reason this file might have to change."
            ),
            measured=float(module.efferent_coupling),
            threshold=float(thresholds.max_efferent_coupling),
        )
        for module in metrics
        if module.efferent_coupling > thresholds.max_efferent_coupling
    ]


def _check_cohesion(
    classes: list[ClassCohesion], thresholds: Thresholds
) -> list[Violation]:
    violations: list[Violation] = []

    for cohesion in classes:
        if cohesion.method_count < thresholds.min_methods_for_cohesion:
            continue
        if cohesion.lcom4 <= thresholds.max_lcom4:
            continue

        groups = ", ".join(
            "{" + ", ".join(component[:3]) + ("...}" if len(component) > 3 else "}")
            for component in cohesion.components[:3]
        )

        violations.append(
            Violation(
                rule="cohesion",
                severity=Severity.WARNING,
                subject=f"{cohesion.path}::{cohesion.class_name}",
                message=(
                    f"{cohesion.method_count} methods form {cohesion.lcom4} unrelated "
                    f"groups: {groups}. This class may be doing "
                    f"{cohesion.lcom4} separate jobs."
                ),
                measured=float(cohesion.lcom4),
                threshold=float(thresholds.max_lcom4),
            )
        )

    return violations


def _check_cycles(cycles: list[list[str]], thresholds: Thresholds) -> list[Violation]:
    severity = Severity.ERROR if thresholds.fail_on_cycles else Severity.WARNING

    return [
        Violation(
            rule="dependency-cycle",
            severity=severity,
            subject=cycle[0],
            message=(
                f"{len(cycle)} files depend on each other in a cycle. "
                f"None can be understood, tested, or changed independently of the rest."
            ),
            measured=float(len(cycle)),
            threshold=0.0,
        )
        for cycle in cycles
    ]


def build_report(
    graph: nx.DiGraph,
    entries: list[SnapshotEntry],
    *,
    extraction: ExtractionReport | None = None,
    thresholds: Thresholds | None = None,
) -> MetricsReport:
    """Compute every metric and evaluate it against the thresholds.

    Args:
        graph: The dependency graph.
        entries: Parsed files, needed for class-level cohesion.
        extraction: Resolution report, so the output can state how much of the
            graph rests on resolved imports.
        thresholds: Policy. Defaults are starting points, not universal truths.
    """
    from raag_tune.dependency_graph import find_cycles

    thresholds = thresholds or Thresholds()

    modules = compute_all_metrics(graph)
    classes = compute_cohesion(entries)
    cycles = find_cycles(graph)

    violations: list[Violation] = []
    violations.extend(_check_cycles(cycles, thresholds))
    violations.extend(_check_instability(modules, thresholds))
    violations.extend(_check_efferent(modules, thresholds))
    violations.extend(_check_cohesion(classes, thresholds))

    # Errors first, then by how far past the threshold the measurement sits.
    violations.sort(
        key=lambda v: (v.severity is not Severity.ERROR, -(v.measured - v.threshold))
    )

    return MetricsReport(
        graph_summary=summarise_graph(graph),
        modules=modules,
        classes=classes,
        violations=violations,
        cycles=cycles,
        resolution_summary=extraction.summary_line() if extraction else "",
        thresholds=thresholds,
    )
