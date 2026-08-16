"""Shared terminal rendering.

All Rich formatting lives here rather than in the command modules. Two reasons:
the commands stay readable as orchestration rather than presentation, and the
output stays visually consistent — a metrics table and a search result table
should look like they came from the same tool.

Every renderer degrades gracefully when piped. Rich detects a non-terminal and
drops colour and box-drawing automatically, which matters because the CI gate
on Day 9 captures this output into a pull request comment where escape codes
would render as garbage.
"""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from raag_master.vector_store import SearchResult
from raag_tune.cohesion import ClassCohesion
from raag_tune.metrics import ModuleMetrics
from raag_tune.report import MetricsReport, Severity

__all__ = [
    "console",
    "error_console",
    "print_cohesion_table",
    "print_coupling_table",
    "print_error",
    "print_graph_summary",
    "print_search_results",
    "print_success",
    "print_violations",
    "print_warning",
]

console = Console()

# Diagnostics go to stderr so that `raag tune ... > report.txt` captures the
# report without the progress chatter mixed into it.
error_console = Console(stderr=True)


def print_error(message: str) -> None:
    error_console.print(f"[bold red]error[/] {message}")


def print_warning(message: str) -> None:
    error_console.print(f"[bold yellow]warning[/] {message}")


def print_success(message: str) -> None:
    console.print(f"[bold green]✓[/] {message}")


def _instability_colour(instability: float) -> str:
    """Colour by severity, not by value.

    Deliberately not a gradient: three bands read faster than a continuous
    scale, and the thresholds match the ones the violation checks use, so the
    colour and the pass/fail verdict never disagree.
    """
    if instability >= 0.7:
        return "red"
    if instability >= 0.4:
        return "yellow"
    return "green"


def print_graph_summary(report: MetricsReport) -> None:
    """Headline figures for the whole repository."""
    lines = "\n".join(report.graph_summary.report_lines())
    if report.resolution_summary:
        lines += f"\nImport resolution  {report.resolution_summary}"

    console.print(
        Panel(lines, title="Dependency graph", title_align="left", expand=False)
    )


def print_coupling_table(
    modules: list[ModuleMetrics], title: str, limit: int = 10
) -> None:
    """Per-module coupling figures.

    Paths are truncated from the left rather than the right. A repository path
    is most distinctive at its end — src/detail/input/parser.hpp and
    src/detail/output/parser.hpp differ in the middle, and truncating the tail
    would render them identical.
    """
    if not modules:
        return

    table = Table(title=title, title_justify="left", header_style="bold")
    table.add_column("Ca", justify="right", width=5)
    table.add_column("Ce", justify="right", width=5)
    table.add_column("I", justify="right", width=6)
    table.add_column("File", overflow="ellipsis", no_wrap=True)

    for module in modules[:limit]:
        table.add_row(
            str(module.afferent_coupling),
            str(module.efferent_coupling),
            Text(
                f"{module.instability:.2f}",
                style=_instability_colour(module.instability),
            ),
            module.path,
        )

    console.print(table)


def print_cohesion_table(classes: list[ClassCohesion], limit: int = 10) -> None:
    """Least cohesive classes, worst first."""
    incohesive = [c for c in classes if not c.is_cohesive][:limit]
    if not incohesive:
        return

    table = Table(
        title="Least cohesive classes", title_justify="left", header_style="bold"
    )
    table.add_column("LCOM4", justify="right", width=7)
    table.add_column("Methods", justify="right", width=8)
    table.add_column("Class", no_wrap=True)
    table.add_column("File", overflow="ellipsis", no_wrap=True)

    for cohesion in incohesive:
        table.add_row(
            Text(str(cohesion.lcom4), style="red" if cohesion.lcom4 > 5 else "yellow"),
            str(cohesion.method_count),
            cohesion.class_name,
            cohesion.path,
        )

    console.print(table)


def print_violations(report: MetricsReport, limit: int = 15) -> None:
    """Threshold breaches, errors first.

    Errors and warnings are visually distinct because they mean different
    things operationally: an error fails the CI gate, a warning does not.
    Rendering them identically would train people to ignore both.
    """
    console.print()

    if not report.violations:
        print_success("All thresholds cleared.")
        return

    header = (
        f"[bold red]{report.error_count} error(s)[/], "
        f"[bold yellow]{report.warning_count} warning(s)[/]"
    )
    console.print(header)
    console.print()

    for violation in report.violations[:limit]:
        is_error = violation.severity is Severity.ERROR
        tag = "[bold red]ERROR[/]" if is_error else "[bold yellow]WARN [/]"
        console.print(f"{tag} [dim]{violation.rule}[/] {violation.subject}")
        console.print(f"      {violation.message}")
        console.print()

    remaining = len(report.violations) - limit
    if remaining > 0:
        console.print(f"[dim]... and {remaining} more[/]")


def print_search_results(results: list[SearchResult], limit: int = 10) -> None:
    """Retrieved chunks with their structural context.

    Metrics are shown alongside every result rather than only the score,
    because relevance alone does not tell a reader whether the match is in a
    file worth changing.
    """
    if not results:
        console.print("[dim]No matches.[/]")
        return

    for rank, result in enumerate(results[:limit], start=1):
        payload = result.payload
        console.print(
            f"[bold]{rank:>2}.[/] [green]{result.score:.3f}[/]  "
            f"{payload.path}:{payload.line_start}-{payload.line_end}"
        )
        console.print(
            f"     [cyan]{payload.qualified_name}[/]  "
            f"[dim]Ca={payload.afferent_coupling} "
            f"Ce={payload.efferent_coupling} "
            f"I={payload.instability:.2f}[/]"
        )

        for line in payload.text.strip().splitlines()[:2]:
            console.print(f"     [dim]│ {line[:78]}[/]")
        console.print()
