"""The `raag master` command group.

Indexing, retrieval, and the blast-radius-scoped refactoring pipeline.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import typer

from raag_cli.display import (
    console,
    print_error,
    print_search_results,
    print_success,
    print_warning,
)
from raag_master.audit import AuditLog
from raag_master.embeddings import Embedder, HashEmbedder, default_embedder
from raag_master.indexer import index_snapshot, search_code
from raag_master.pipeline import run_refactor
from raag_master.reasoning import AnthropicReasoner, DryRunReasoner, Reasoner
from raag_master.vector_store import VectorStore
from raag_tune.dependency_graph import build_dependency_graph
from raag_tune.snapshot_reader import SnapshotError, read_snapshot

__all__ = ["app"]

app = typer.Typer(help="Index code and run blast-radius-scoped refactoring.")

_DEFAULT_SNAPSHOT = Path("snapshots/repo.raag.bin")
_DEFAULT_COLLECTION = "raag_code"
_DEFAULT_AUDIT_DB = Path("audit.db")
_DEFAULT_QDRANT = "http://localhost:6333"


def _load(snapshot: Path) -> Any:
    try:
        return read_snapshot(snapshot)
    except FileNotFoundError:
        print_error(f"{snapshot} does not exist. Run `raag sample run` first.")
        raise typer.Exit(code=2) from None
    except SnapshotError as error:
        print_error(str(error))
        raise typer.Exit(code=2) from None


def _embedder(offline: bool) -> Embedder:
    return HashEmbedder() if offline else default_embedder()


def _store(collection: str, embedder: Embedder, url: str) -> VectorStore:
    """Connect, checking reachability first.

    A stopped Qdrant container is the single most common failure when picking
    this project back up after a break, and the raw client error is a wall of
    httpx traceback that says nothing actionable. Failing early with the fix
    in the message costs one extra request and saves the traceback entirely.
    """
    store = VectorStore.connect(
        collection=collection,
        dimensions=embedder.dimensions,
        url=url,
        embedder_name=embedder.name,
    )

    try:
        store.count()
    except Exception:
        print_error(f"could not reach Qdrant at {url}.")
        print_warning(
            "Start it with:\n"
            "  docker start raag-qdrant\n"
            "Or create it with a restart policy so this does not recur:\n"
            "  docker run -d -p 6333:6333 -v $(pwd)/qdrant_storage:/qdrant/storage \\\n"
            "    --restart unless-stopped --name raag-qdrant qdrant/qdrant"
        )
        raise typer.Exit(code=2) from None

    return store


@app.command("index")
def index(
    snapshot: Path = typer.Argument(_DEFAULT_SNAPSHOT),
    repo_root: Path = typer.Option(
        Path(), "--repo-root", help="Directory the snapshot's paths resolve against."
    ),
    collection: str = typer.Option(_DEFAULT_COLLECTION, "--collection"),
    qdrant_url: str = typer.Option(_DEFAULT_QDRANT, "--qdrant-url"),
    recreate: bool = typer.Option(
        False, "--recreate", help="Drop and rebuild. Required after changing embedder."
    ),
    offline: bool = typer.Option(
        False,
        "--offline",
        help="Use the non-semantic hash embedder. No model download, but "
        "retrieval degrades to vocabulary overlap.",
    ),
) -> None:
    """Chunk, embed, and store a snapshot's code."""
    entries = _load(snapshot)
    graph, _ = build_dependency_graph(entries)
    embedder = _embedder(offline)
    store = _store(collection, embedder, qdrant_url)

    if offline:
        print_warning("offline mode: retrieval will not be semantic.")

    console.print(f"Indexing {len(entries)} files from [dim]{snapshot}[/]\n")

    report = index_snapshot(
        entries, graph, store, embedder, repo_root=repo_root, recreate=recreate
    )

    console.print()
    for line in report.summary_lines():
        console.print(line)

    print_success(f"Collection holds {store.count():,} points.")


@app.command("search")
def search(
    query: str = typer.Argument(..., help="What to look for, in plain language."),
    limit: int = typer.Option(10, "--limit", "-n"),
    path: list[str] = typer.Option(
        None, "--path", help="Restrict to these files. Repeatable."
    ),
    collection: str = typer.Option(_DEFAULT_COLLECTION, "--collection"),
    qdrant_url: str = typer.Option(_DEFAULT_QDRANT, "--qdrant-url"),
    offline: bool = typer.Option(False, "--offline"),
) -> None:
    """Search indexed code semantically."""
    embedder = _embedder(offline)
    store = _store(collection, embedder, qdrant_url)

    results = search_code(query, store, embedder, limit=limit, paths=path or None)

    console.print(f'[bold]Query:[/] "{query}"')
    if path:
        console.print(f"[dim]Restricted to {len(path)} file(s)[/]")
    console.print()

    print_search_results(results, limit)


@app.command("refactor")
def refactor(
    target: str = typer.Argument(..., help="File the request is about."),
    request: str = typer.Argument(..., help="What you want done, in plain language."),
    snapshot: Path = typer.Option(_DEFAULT_SNAPSHOT, "--snapshot"),
    depth: int = typer.Option(2, "--depth", help="Blast radius traversal depth."),
    limit: int = typer.Option(15, "--limit", "-n", help="Chunks to retrieve."),
    model: str | None = typer.Option(None, "--model", help="Override the model."),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Assemble the prompt without calling the API. Free and instant — "
        "use this to verify prompt assembly before spending anything.",
    ),
    show_prompt: bool = typer.Option(False, "--show-prompt"),
    audit_db: Path = typer.Option(_DEFAULT_AUDIT_DB, "--audit-db"),
    no_audit: bool = typer.Option(False, "--no-audit"),
    collection: str = typer.Option(_DEFAULT_COLLECTION, "--collection"),
    qdrant_url: str = typer.Option(_DEFAULT_QDRANT, "--qdrant-url"),
    offline: bool = typer.Option(False, "--offline"),
) -> None:
    """Run the full blast-radius-scoped refactoring pipeline."""
    entries = _load(snapshot)
    graph, _ = build_dependency_graph(entries)
    embedder = _embedder(offline)
    store = _store(collection, embedder, qdrant_url)

    reasoner: Reasoner
    if dry_run:
        reasoner = DryRunReasoner()
    else:
        try:
            reasoner = AnthropicReasoner(model=model)
        except RuntimeError as error:
            print_error(str(error))
            raise typer.Exit(code=2) from None

    try:
        outcome = run_refactor(
            request,
            target,
            graph,
            store,
            embedder,
            reasoner,
            audit_path=None if no_audit else audit_db,
            depth=depth,
            retrieval_limit=limit,
        )
    except KeyError as error:
        print_error(str(error))
        raise typer.Exit(code=2) from None

    console.print()
    for line in outcome.summary_lines():
        console.print(line)
    console.print()

    if show_prompt:
        console.print("[bold]Assembled prompt[/]")
        console.print(outcome.context.prompt)
        console.print()

    if outcome.succeeded:
        console.print(outcome.reasoning.text)
    else:
        print_error(f"reasoning failed: {outcome.reasoning.error}")
        raise typer.Exit(code=1)


@app.command("audit")
def audit(
    record_id: int | None = typer.Option(None, "--record-id", help="Show one record."),
    target: str | None = typer.Option(None, "--target", help="Filter by file."),
    failures_only: bool = typer.Option(
        False, "--failures", help="Only failed requests."
    ),
    limit: int = typer.Option(20, "--limit", "-n"),
    show_prompt: bool = typer.Option(False, "--show-prompt"),
    audit_db: Path = typer.Option(_DEFAULT_AUDIT_DB, "--audit-db"),
) -> None:
    """Inspect the reasoning audit log."""
    log = AuditLog(audit_db)

    if record_id is not None:
        record = log.get(record_id)
        if record is None:
            print_error(f"no audit record #{record_id}")
            raise typer.Exit(code=2)

        console.print(f"[bold]Record[/]   #{record.record_id}")
        console.print(f"[bold]When[/]     {record.created_at}")
        console.print(f"[bold]Target[/]   {record.target}")
        console.print(f"[bold]Model[/]    {record.model}")
        console.print(f"[bold]Request[/]  {record.request}")
        console.print(
            f"[bold]Metrics[/]  Ca={record.target_afferent} "
            f"Ce={record.target_efferent} I={record.target_instability:.2f}"
        )
        console.print(
            f"[bold]Radius[/]   {len(record.dependents)} dependents, "
            f"{len(record.dependencies)} dependencies"
        )
        console.print(
            f"[bold]Chunks[/]   {len(record.retrieved_chunks)} used, "
            f"{len(record.excluded_chunks)} dropped"
        )
        if record.error:
            console.print(f"[bold red]Error[/]    {record.error}")

        if show_prompt:
            console.print("\n[bold]Prompt[/]")
            console.print(record.prompt)
        if record.response:
            console.print("\n[bold]Response[/]")
            console.print(record.response)
        return

    if failures_only:
        records = log.failures(limit=limit)
    elif target:
        records = log.for_target(target, limit=limit)
    else:
        records = log.recent(limit=limit)

    if not records:
        console.print("[dim]No audit records.[/]")
        return

    console.print(f"{log.count()} record(s) total. Showing {len(records)}.\n")

    for record in records:
        status = "[red]FAILED[/]" if record.error else "[green]ok[/]"
        console.print(
            f"[bold]#{record.record_id}[/] {record.created_at[:19]}  {status}"
        )
        console.print(f"     {record.target}")
        console.print(f"     [dim]{record.request[:70]}[/]")
        console.print()
