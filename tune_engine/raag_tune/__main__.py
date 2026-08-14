"""Command-line entry point for the Tune Engine.

Provisional. The full Typer interface arrives on Day 8; this exists so the
analytics are exercisable end to end today.

    python -m raag_tune snapshots/repo.raag.bin
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import networkx as nx

from raag_tune.dependency_graph import build_dependency_graph, topological_layers
from raag_tune.report import MetricsReport, Thresholds, build_report
from raag_tune.snapshot_reader import SnapshotError, read_snapshot

_RULE = "-" * 62


def _export_graph(graph: nx.DiGraph, destination: Path) -> None:
    """Write the graph as JSON.

    Edge ``kinds`` is a set, which JSON cannot represent, so it is sorted into
    a list — sorted rather than arbitrary so the output is byte-stable and
    diffable between runs.
    """
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
    destination.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _print_graph_section(report: MetricsReport) -> None:
    print("Dependency graph")
    print(_RULE)
    for line in report.graph_summary.report_lines():
        print(line)
    if report.resolution_summary:
        print(f"Import resolution  {report.resolution_summary}")


def _print_coupling_section(report: MetricsReport, limit: int) -> None:
    unstable = report.most_unstable(limit)
    if not unstable:
        return

    print()
    print(f"Most unstable depended-upon modules (top {len(unstable)})")
    print(_RULE)
    print(f"{'Ca':>4} {'Ce':>4} {'I':>6}   File")
    for module in unstable:
        print(
            f"{module.afferent_coupling:>4} "
            f"{module.efferent_coupling:>4} "
            f"{module.instability:>6.2f}   {module.path}"
        )


def _print_foundations_section(report: MetricsReport, limit: int) -> None:
    depended = [m for m in report.most_depended_upon(limit) if m.afferent_coupling > 0]
    if not depended:
        return

    print()
    print(f"Most depended-upon modules (top {len(depended)})")
    print(_RULE)
    print(f"{'Ca':>4} {'Ce':>4} {'I':>6}   File")
    for module in depended:
        print(
            f"{module.afferent_coupling:>4} "
            f"{module.efferent_coupling:>4} "
            f"{module.instability:>6.2f}   {module.path}"
        )


def _print_cohesion_section(report: MetricsReport, limit: int) -> None:
    incohesive = report.least_cohesive(limit)
    if not incohesive:
        return

    print()
    print(f"Least cohesive classes (top {len(incohesive)})")
    print(_RULE)
    print(f"{'LCOM4':>6} {'Methods':>8}   Class")
    for cohesion in incohesive:
        print(
            f"{cohesion.lcom4:>6} {cohesion.method_count:>8}   "
            f"{cohesion.class_name}  ({cohesion.path})"
        )


def _print_violations_section(report: MetricsReport, limit: int) -> None:
    print()
    print(
        f"Violations: {report.error_count} error(s), {report.warning_count} warning(s)"
    )
    print(_RULE)

    if not report.violations:
        print("None. All thresholds cleared.")
        return

    for violation in report.violations[:limit]:
        print(violation.format_line())

    remaining = len(report.violations) - limit
    if remaining > 0:
        print(f"\n... and {remaining} more")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="raag_tune",
        description="Compute architectural metrics from a RAAG AST snapshot.",
    )
    parser.add_argument("snapshot", type=Path, help="Path to a .raag.bin snapshot")
    parser.add_argument("--export-graph", type=Path, help="Write the graph as JSON")
    parser.add_argument("--export-metrics", type=Path, help="Write the report as JSON")
    parser.add_argument(
        "--top", type=int, default=10, metavar="N", help="Rows per table (default: 10)"
    )
    parser.add_argument(
        "--max-instability",
        type=float,
        default=0.4,
        metavar="I",
        help="Instability limit for depended-upon modules (default: 0.4)",
    )
    parser.add_argument(
        "--fail-on-violations",
        action="store_true",
        help="Exit non-zero when any error-severity violation is found",
    )

    args = parser.parse_args(argv)

    try:
        entries = read_snapshot(args.snapshot)
    except SnapshotError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    except FileNotFoundError:
        print(f"error: {args.snapshot} does not exist", file=sys.stderr)
        return 2

    graph, extraction = build_dependency_graph(entries)
    report = build_report(
        graph,
        entries,
        extraction=extraction,
        thresholds=Thresholds(max_instability=args.max_instability),
    )

    total_nodes = sum(len(entry.arena) for entry in entries)
    print(f"Snapshot   {args.snapshot}")
    print(f"Parsed     {len(entries)} files, {total_nodes} AST nodes")
    print()

    _print_graph_section(report)
    _print_coupling_section(report, args.top)
    _print_foundations_section(report, args.top)
    _print_cohesion_section(report, args.top)
    _print_violations_section(report, args.top)

    layers = topological_layers(graph)
    if layers:
        print()
        print("Dependency layers (foundations first)")
        print(_RULE)
        for index, layer in enumerate(layers[:8]):
            print(f"  Layer {index}: {len(layer)} files")
        if len(layers) > 8:
            print(f"  ... and {len(layers) - 8} more layers")

    if args.export_graph:
        _export_graph(graph, args.export_graph)
        print(f"\nGraph written to {args.export_graph}")

    if args.export_metrics:
        report.write_json(args.export_metrics)
        print(f"Metrics written to {args.export_metrics}")

    if args.fail_on_violations and not report.passed:
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
