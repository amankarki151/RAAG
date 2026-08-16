"""The `raag tune` command group.

Graph construction and metrics. The exit code is the load-bearing part here:
Day 9's CI gate runs `raag tune` and decides whether to block a merge based
entirely on what this returns, so the mapping from findings to exit status is
an interface contract, not a convenience.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import typer

from raag_cli.display import (
    console,
    print_cohesion_table,
    print_coupling_table,
    print_error,
    print_graph_summary,
    print_success,
    print_violations,
)
from raag_tune.dependency_graph import build_dependency_graph, topological_layers
from raag_tune.report import Thresholds, build_report
from raag_tune.snapshot_reader import SnapshotError, read_snapshot

__all__ = ["app"]

app = typer.Typer(help="Build the dependency graph and compute metrics.")


def _load(snapshot: Path) -> Any:
    try:
        return read_snapshot(snapshot)
    except FileNotFoundError:
        print_error(f"{snapshot} does not exist. Run `raag sample run` first.")
        raise typer.Exit(code=2) from None
    except SnapshotError as error:
        print_error(str(error))
        raise typer.Exit(code=2) from None


@app.command("run")
def run(
    snapshot: Path = typer.Argument(
        Path("snapshots/repo.raag.bin"), help="Snapshot to analyse."
    ),
    top: int = typer.Option(10, "--top", "-n", help="Rows per table."),
    max_instability: float = typer.Option(
        0.4, "--max-instability", help="Instability limit for depended-upon modules."
    ),
    min_afferent: int = typer.Option(
        3,
        "--min-afferent",
        help="Afferent coupling below which instability is not judged. "
        "An unstable leaf is not a defect.",
    ),
    fail_on_instability: bool = typer.Option(
        False,
        "--fail-on-instability",
        help="Exit non-zero when any error-severity violation is found. "
        "This is what the CI gate uses.",
    ),
    export_metrics: Path | None = typer.Option(
        None, "--export-metrics", help="Write the full report as JSON."
    ),
    export_graph: Path | None = typer.Option(
        None, "--export-graph", help="Write the dependency graph as JSON."
    ),
    show_layers: bool = typer.Option(
        False, "--show-layers", help="Print dependency layers, foundations first."
    ),
) -> None:
    """Analyse a snapshot and report architectural metrics."""
    entries = _load(snapshot)
    graph, extraction = build_dependency_graph(entries)

    report = build_report(
        graph,
        entries,
        extraction=extraction,
        thresholds=Thresholds(
            max_instability=max_instability,
            min_afferent_for_instability=min_afferent,
        ),
    )

    total_nodes = sum(len(entry.arena) for entry in entries)
    console.print(
        f"[dim]{snapshot}[/] — {len(entries)} files, {total_nodes:,} AST nodes\n"
    )

    print_graph_summary(report)
    console.print()

    print_coupling_table(
        report.most_unstable(top), "Most unstable depended-upon modules", top
    )
    console.print()
    print_coupling_table(
        report.most_depended_upon(top), "Most depended-upon modules", top
    )
    console.print()
    print_cohesion_table(report.classes, top)

    print_violations(report)

    if show_layers:
        layers = topological_layers(graph)
        console.print()
        console.print("[bold]Dependency layers[/] [dim](foundations first)[/]")
        for index, layer in enumerate(layers[:10]):
            console.print(f"  Layer {index}: {len(layer)} files")
        if len(layers) > 10:
            console.print(f"  [dim]... and {len(layers) - 10} more[/]")

    if export_metrics:
        report.write_json(export_metrics)
        print_success(f"Metrics written to {export_metrics}")

    if export_graph:
        _export_graph(graph, export_graph)
        print_success(f"Graph written to {export_graph}")

    # Only error-severity findings fail the run. Warnings surface signals worth
    # looking at without blocking anything, because a gate that fires on every
    # soft signal gets disabled, and a disabled gate protects nothing.
    if fail_on_instability and not report.passed:
        raise typer.Exit(code=1)


@app.command("cycles")
def cycles(
    snapshot: Path = typer.Argument(Path("snapshots/repo.raag.bin")),
    limit: int = typer.Option(10, "--limit", "-n"),
) -> None:
    """List dependency cycles, largest first."""
    from raag_tune.dependency_graph import find_cycles

    entries = _load(snapshot)
    graph, _ = build_dependency_graph(entries)
    found = find_cycles(graph, limit=limit)

    if not found:
        print_success("No dependency cycles found.")
        return

    console.print(f"[bold red]{len(found)} cycle(s) found[/]\n")

    for index, cycle in enumerate(found, start=1):
        console.print(f"[bold]{index}.[/] {len(cycle)} files")
        for path in cycle[:8]:
            console.print(f"     {path}")
        if len(cycle) > 8:
            console.print(f"     [dim]... and {len(cycle) - 8} more[/]")
        console.print()


def _export_graph(graph: Any, destination: Path) -> None:
    """Write the graph as byte-stable JSON.

    Sorted throughout so two runs over unchanged code produce identical files —
    otherwise a metrics diff between commits is unreadable noise.
    """
    import json

    payload = {
        "nodes": [{"path": node, **graph.nodes[node]} for node in sorted(graph.nodes)],
        "edges": [
            {
                "source": source,
                "target": target,
                "weight": data["weight"],
                "kinds": sorted(str(kind) for kind in data["kinds"]),
            }
            for source, target, data in sorted(graph.edges(data=True))
        ],
    }

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"
    )
