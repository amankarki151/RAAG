"""Command-line entry point for the Master Engine.

Provisional. The unified Typer interface arrives on Day 8.

    python -m raag_master index snapshots/repo.raag.bin
    python -m raag_master search "how are parse errors reported"
    python -m raag_master refactor <file> "reduce this file's coupling" --dry-run

Global flags (--offline, --qdrant-url, --collection) work before OR after the
subcommand name.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

from raag_master.audit import AuditLog
from raag_master.embeddings import Embedder, HashEmbedder, default_embedder
from raag_master.indexer import index_snapshot, search_code
from raag_master.pipeline import run_refactor
from raag_master.reasoning import AnthropicReasoner, DryRunReasoner, Reasoner
from raag_master.vector_store import VectorStore
from raag_tune.ast_types import SnapshotEntry
from raag_tune.dependency_graph import build_dependency_graph
from raag_tune.snapshot_reader import SnapshotError, read_snapshot

_RULE = "-" * 62
_DEFAULT_COLLECTION = "raag_code"
_DEFAULT_AUDIT_DB = Path("audit.db")


def _make_embedder(offline: bool) -> Embedder:
    return HashEmbedder() if offline else default_embedder()


def _make_store(args: argparse.Namespace, embedder: Embedder) -> VectorStore:
    return VectorStore.connect(
        collection=args.collection,
        dimensions=embedder.dimensions,
        url=args.qdrant_url,
        embedder_name=embedder.name,
    )


def _load_entries(snapshot: Path) -> list[SnapshotEntry]:
    try:
        return read_snapshot(snapshot)
    except SnapshotError as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(2) from error
    except FileNotFoundError as error:
        print(f"error: {snapshot} does not exist", file=sys.stderr)
        raise SystemExit(2) from error


def _command_index(args: argparse.Namespace) -> int:
    entries = _load_entries(args.snapshot)
    graph, _ = build_dependency_graph(entries)
    embedder = _make_embedder(args.offline)
    store = _make_store(args, embedder)

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
    store = _make_store(args, embedder)

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
        for line in payload.text.strip().splitlines()[:3]:
            print(f"      | {line[:80]}")
        print()

    return 0


def _command_refactor(args: argparse.Namespace) -> int:
    entries = _load_entries(args.snapshot)
    graph, _ = build_dependency_graph(entries)

    embedder = _make_embedder(args.offline)
    store = _make_store(args, embedder)

    reasoner: Reasoner
    if args.dry_run:
        reasoner = DryRunReasoner()
    else:
        try:
            reasoner = AnthropicReasoner(model=args.model)
        except RuntimeError as error:
            print(f"error: {error}", file=sys.stderr)
            return 2

    try:
        outcome = run_refactor(
            args.request,
            args.target,
            graph,
            store,
            embedder,
            reasoner,
            audit_path=None if args.no_audit else args.audit_db,
            depth=args.depth,
            retrieval_limit=args.limit,
        )
    except KeyError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    print(_RULE)
    for line in outcome.summary_lines():
        print(line)
    print(_RULE)
    print()

    if args.show_prompt:
        print("=== ASSEMBLED PROMPT " + "=" * 41)
        print(outcome.context.prompt)
        print("=" * 62)
        print()

    if outcome.succeeded:
        print(outcome.reasoning.text)
    else:
        print(f"Reasoning failed: {outcome.reasoning.error}", file=sys.stderr)
        return 1

    return 0


def _command_audit(args: argparse.Namespace) -> int:
    log = AuditLog(args.audit_db)

    if args.record_id is not None:
        record = log.get(args.record_id)
        if record is None:
            print(f"error: no audit record #{args.record_id}", file=sys.stderr)
            return 2

        print(f"Record   #{record.record_id}")
        print(f"When     {record.created_at}")
        print(f"Target   {record.target}")
        print(f"Model    {record.model}")
        print(f"Request  {record.request}")
        print(
            f"Metrics  Ca={record.target_afferent} Ce={record.target_efferent} "
            f"I={record.target_instability:.2f}"
        )
        print(
            f"Radius   {len(record.dependents)} dependents, "
            f"{len(record.dependencies)} dependencies"
        )
        print(
            f"Chunks   {len(record.retrieved_chunks)} used, "
            f"{len(record.excluded_chunks)} dropped"
        )
        if record.error:
            print(f"Error    {record.error}")
        if args.show_prompt:
            print()
            print("=== PROMPT " + "=" * 51)
            print(record.prompt)
        if record.response:
            print()
            print("=== RESPONSE " + "=" * 49)
            print(record.response)
        return 0

    records = (
        log.for_target(args.target, limit=args.limit)
        if args.target
        else log.recent(limit=args.limit)
    )

    if not records:
        print("No audit records.")
        return 0

    print(f"{log.count()} record(s) total. Showing {len(records)}.")
    print(_RULE)
    for record in records:
        status = "FAILED" if record.error else "ok"
        print(
            f"#{record.record_id:<5} {record.created_at[:19]}  {status:<7} "
            f"{record.target}"
        )
        print(f"       {record.request[:70]}")

    return 0


def _build_parser() -> argparse.ArgumentParser:
    # Shared flags are defined on the top-level parser normally, and on each
    # subparser with default=SUPPRESS. When a subcommand runs, argparse parses
    # its arguments into a brand-new namespace and copies every value onto the
    # real one unconditionally — so without SUPPRESS, a subparser's default
    # silently overwrites whatever the top-level parser already set, whatever
    # order the flags were typed in.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--qdrant-url", default="http://localhost:6333")
    common.add_argument("--collection", default=_DEFAULT_COLLECTION)
    common.add_argument(
        "--offline",
        action="store_true",
        help="Use the non-semantic hash embedder. No model download; "
        "retrieval degrades to vocabulary overlap.",
    )

    parser = argparse.ArgumentParser(
        prog="raag_master",
        description="Index, search, and reason about a codebase.",
        parents=[common],
    )

    def add_shared(subparser: argparse.ArgumentParser) -> None:
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
    add_shared(index)
    index.add_argument("snapshot", type=Path)
    index.add_argument("--repo-root", type=Path, default=Path())
    index.add_argument("--recreate", action="store_true")
    index.add_argument("--no-progress", action="store_true")
    index.set_defaults(func=_command_index)

    search = subparsers.add_parser("search", help="Search indexed code")
    add_shared(search)
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=10)
    search.add_argument("--path", action="append")
    search.set_defaults(func=_command_search)

    refactor = subparsers.add_parser(
        "refactor", help="Run the full blast-radius-scoped pipeline"
    )
    add_shared(refactor)
    refactor.add_argument("target", help="File the request is about")
    refactor.add_argument("request", help="What you want done, in plain language")
    refactor.add_argument(
        "--snapshot", type=Path, default=Path("snapshots/repo.raag.bin")
    )
    refactor.add_argument("--depth", type=int, default=2)
    refactor.add_argument("--limit", type=int, default=15)
    refactor.add_argument("--model", default=None)
    refactor.add_argument(
        "--dry-run",
        action="store_true",
        help="Assemble the prompt without calling the API. Free and instant.",
    )
    refactor.add_argument(
        "--show-prompt", action="store_true", help="Print the assembled prompt"
    )
    refactor.add_argument("--audit-db", type=Path, default=_DEFAULT_AUDIT_DB)
    refactor.add_argument("--no-audit", action="store_true")
    refactor.set_defaults(func=_command_refactor)

    audit = subparsers.add_parser("audit", help="Inspect the audit log")
    audit.add_argument("--audit-db", type=Path, default=_DEFAULT_AUDIT_DB)
    audit.add_argument("--record-id", type=int, default=None)
    audit.add_argument("--target", default=None)
    audit.add_argument("--limit", type=int, default=20)
    audit.add_argument("--show-prompt", action="store_true")
    audit.set_defaults(func=_command_audit)

    return parser


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = _build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
