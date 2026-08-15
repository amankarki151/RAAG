"""Command-line entry point for the Master Engine.

Provisional. The unified Typer interface arrives on Day 8.

    python -m raag_master index snapshots/repo.raag.bin
    python -m raag_master search "how are parse errors reported"

Global flags (--offline, --qdrant-url, --collection) work before OR after the
subcommand name — both forms are accepted:

    python -m raag_master --offline index snapshots/repo.raag.bin
    python -m raag_master index snapshots/repo.raag.bin --offline
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from raag_master.embeddings import Embedder, HashEmbedder, default_embedder
from raag_master.indexer import index_snapshot, search_code
from raag_master.vector_store import VectorStore
from raag_tune.dependency_graph import build_dependency_graph
from raag_tune.snapshot_reader import SnapshotError, read_snapshot

_RULE = "-" * 62
_DEFAULT_COLLECTION = "raag_code"


def _make_embedder(offline: bool) -> HashEmbedder | Embedder:
    return HashEmbedder() if offline else default_embedder()


def _command_index(args: argparse.Namespace) -> int:
    try:
        entries = read_snapshot(args.snapshot)
    except SnapshotError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    except FileNotFoundError:
        print(f"error: {args.snapshot} does not exist", file=sys.stderr)
        return 2

    graph, _ = build_dependency_graph(entries)
    embedder = _make_embedder(args.offline)

    store = VectorStore.connect(
        collection=args.collection,
        dimensions=embedder.dimensions,
        url=args.qdrant_url,
        embedder_name=embedder.name,
    )

    print(f"Indexing {len(entries)} files from {args.snapshot}")
    print(_RULE)

    report = index_snapshot(
        entries,
        graph,
        store,
        embedder,
        repo_root=args.repo_root,
        recreate=args.recreate,
        show_progress=not args.no_progress,
    )

    for line in report.summary_lines():
        print(line)

    print()
    print(f"Collection now holds {store.count()} points.")
    return 0


def _command_search(args: argparse.Namespace) -> int:
    embedder = _make_embedder(args.offline)

    store = VectorStore.connect(
        collection=args.collection,
        dimensions=embedder.dimensions,
        url=args.qdrant_url,
        embedder_name=embedder.name,
    )

    paths = args.path or None
    results = search_code(args.query, store, embedder, limit=args.limit, paths=paths)

    print(f'Query: "{args.query}"')
    if paths:
        print(f"Restricted to {len(paths)} file(s)")
    print(_RULE)

    if not results:
        print("No matches.")
        return 0

    for rank, result in enumerate(results, start=1):
        payload = result.payload
        print(f"{rank:>2}. {result.score:.3f}  {result.citation()}")
        print(
            f"      Ca={payload.afferent_coupling} "
            f"Ce={payload.efferent_coupling} "
            f"I={payload.instability:.2f}"
        )

        preview = payload.text.strip().splitlines()[:3]
        for line in preview:
            print(f"      | {line[:80]}")
        print()

    return 0


def _build_parser() -> argparse.ArgumentParser:
    # Shared flags are defined on the top-level parser normally, and on each
    # subparser with default=SUPPRESS.
    #
    # This matters because of a genuinely obscure argparse behavior: when a
    # subcommand is invoked, argparse parses its arguments into a BRAND NEW
    # namespace (not the shared one), then copies every value from that fresh
    # namespace onto the real one — unconditionally, even for flags the user
    # never typed after the subcommand. Without SUPPRESS, the subparser's own
    # default (False) always overwrites whatever the top-level parser already
    # set, regardless of argument order. With SUPPRESS, a flag not explicitly
    # given at the subparser level is simply absent from that fresh namespace,
    # so there is nothing to overwrite the top-level value with. A flag given
    # explicitly at the subparser level still sets and wins, as expected.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--qdrant-url",
        default="http://localhost:6333",
        help="Qdrant endpoint (default: http://localhost:6333)",
    )
    common.add_argument(
        "--collection",
        default=_DEFAULT_COLLECTION,
        help=f"Collection name (default: {_DEFAULT_COLLECTION})",
    )
    common.add_argument(
        "--offline",
        action="store_true",
        help="Use the non-semantic hash embedder. Fast, no model download, "
        "but retrieval degrades to vocabulary overlap.",
    )

    parser = argparse.ArgumentParser(
        prog="raag_master",
        description="Index and retrieve code chunks for RAAG.",
        parents=[common],
    )

    def add_shared_flags(subparser: argparse.ArgumentParser) -> None:
        subparser.add_argument(
            "--qdrant-url", default=argparse.SUPPRESS, help=argparse.SUPPRESS
        )
        subparser.add_argument(
            "--collection", default=argparse.SUPPRESS, help=argparse.SUPPRESS
        )
        subparser.add_argument(
            "--offline",
            action="store_true",
            default=argparse.SUPPRESS,
            help=argparse.SUPPRESS,
        )

    subparsers = parser.add_subparsers(dest="command", required=True)

    index = subparsers.add_parser("index", help="Index a snapshot")
    add_shared_flags(index)
    index.add_argument("snapshot", type=Path)
    index.add_argument(
        "--repo-root",
        type=Path,
        default=Path(),
        help="Directory the snapshot's relative paths resolve against",
    )
    index.add_argument(
        "--recreate",
        action="store_true",
        help="Drop and rebuild the collection. Required after changing embedder.",
    )
    index.add_argument(
        "--no-progress",
        action="store_true",
        help="Suppress the tqdm progress bar (useful when piping output to a file).",
    )
    index.set_defaults(func=_command_index)

    search = subparsers.add_parser("search", help="Search indexed code")
    add_shared_flags(search)
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=10)
    search.add_argument(
        "--path",
        action="append",
        help="Restrict to this file. Repeatable. Previews the blast-radius "
        "scoping the refactoring pipeline uses.",
    )
    search.set_defaults(func=_command_search)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
