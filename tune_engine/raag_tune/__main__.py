"""Command-line entry point for the Tune Engine.

Provisional. The full Typer interface arrives on Day 8; this exists so the
graph layer is exercisable end to end today.

    python -m raag_tune snapshots/repo.raag.bin
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import networkx as nx

from raag_tune.dependency_graph import (
    build_dependency_graph,
    find_cycles,
    summarise_graph,
    topological_layers,
)
from raag_tune.snapshot_reader import SnapshotError, read_snapshot


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="raag_tune",
        description="Build a dependency graph from a RAAG AST snapshot.",
    )
    parser.add_argument("snapshot", type=Path, help="Path to a .raag.bin snapshot")
    parser.add_argument(
        "--export", type=Path, help="Write the graph as JSON to this path"
    )
    parser.add_argument(
        "--show-cycles",
        type=int,
        default=5,
        metavar="N",
        help="Print the N largest dependency cycles (default: 5)",
    )
    parser.add_argument(
        "--show-unresolved",
        type=int,
        default=10,
        metavar="N",
        help="Print the N most frequent unresolved imports (default: 10)",
    )

    args = parser.parse_args(argv)

    try:
        entries = read_snapshot(args.snapshot)
    except SnapshotError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    except FileNotFoundError:
        print(f"error: {args.snapshot} does not exist", file=sys.stderr)
        return 1

    graph, report = build_dependency_graph(entries)
    summary = summarise_graph(graph)

    print(f"Snapshot   {args.snapshot}")
    print(
        f"Parsed     {len(entries)} files, {sum(len(e.arena) for e in entries)} nodes"
    )
    print()
    print("Dependency graph")
    print("-" * 46)
    for line in summary.report_lines():
        print(line)
    print()
    print("Import resolution")
    print("-" * 46)
    print(report.summary_line())

    if args.show_unresolved and report.unresolved_targets:
        print()
        print(f"Most frequent external imports (top {args.show_unresolved})")
        print("-" * 46)
        for target, count in report.unresolved_targets.most_common(
            args.show_unresolved
        ):
            print(f"  {count:>5}  {target}")

    cycles = find_cycles(graph, limit=args.show_cycles)
    if cycles:
        print()
        print(f"Dependency cycles (largest {len(cycles)})")
        print("-" * 46)
        for index, cycle in enumerate(cycles, start=1):
            print(f"  {index}. {len(cycle)} files")
            for path in cycle[:6]:
                print(f"       {path}")
            if len(cycle) > 6:
                print(f"       ... and {len(cycle) - 6} more")

    layers = topological_layers(graph)
    if layers:
        print()
        print("Dependency layers (foundations first)")
        print("-" * 46)
        for index, layer in enumerate(layers[:8]):
            print(f"  Layer {index}: {len(layer)} files")
        if len(layers) > 8:
            print(f"  ... and {len(layers) - 8} more layers")

    if args.export:
        _export_graph(graph, args.export)
        print()
        print(f"Graph written to {args.export}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
