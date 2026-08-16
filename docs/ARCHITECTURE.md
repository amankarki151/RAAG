# Architecture

How RAAG is put together, and why each boundary sits where it does.

This document explains structure and rationale. For the exact byte layout of
the inter-engine format see [CONTRACTS.md](CONTRACTS.md); for metric
definitions see [METRICS.md](METRICS.md).

---

## Table of Contents

- [The shape of the system](#the-shape-of-the-system)
- [Why three engines](#why-three-engines)
- [Engine boundaries](#engine-boundaries)
- [Data flow](#data-flow)
- [Cross-cutting decisions](#cross-cutting-decisions)
- [What is deliberately not here](#what-is-deliberately-not-here)

---

## The shape of the system

```
                        ┌─────────────────┐
   source repository ──►│  SAMPLE ENGINE  │  C++20
                        │   extraction    │  Tree-sitter, std::jthread
                        └────────┬────────┘
                                 │  binary AST snapshot
                                 │  (versioned; see CONTRACTS.md)
                        ┌────────▼────────┐
                        │   TUNE ENGINE   │  Python 3.12
                        │    analytics    │  NetworkX
                        └────────┬────────┘
                                 │  dependency graph + metrics
                                 │
                        ┌────────▼────────┐
                        │  MASTER ENGINE  │  Python 3.12
                        │  orchestration  │  Qdrant, SQLite, LLM
                        └────────┬────────┘
                                 │
                                 ▼
                    refactoring plan + audit record
```

One `raag` CLI sits across all three. Each engine is independently buildable,
independently testable, and replaceable without touching its neighbours.

---

## Why three engines

The split is by **workload characteristics**, not by arbitrary layering.

**Extraction is throughput-bound.** It walks a filesystem, reads every source
file, and parses each one. It needs no ecosystem breadth — just speed and
predictable memory. C++20 with a `std::jthread` worker pool is the right tool,
and an interpreted parser over a million-line repository produces wall-clock
times nobody tolerates in a pre-commit hook.

**Analytics is algorithm-bound.** Graph construction, strongly connected
components, topological layering, coupling arithmetic. Here library maturity
matters far more than raw speed — NetworkX gives correct, well-tested
implementations of algorithms that are easy to get subtly wrong by hand.

**Orchestration is integration-bound.** Vector stores, embedding models, LLM
APIs, SQLite. This is the layer most likely to change as tooling evolves, so
it optimises for how quickly a component can be swapped.

Writing all three in one language would compromise at least one of them.

---

## Engine boundaries

### Sample Engine — extraction

**Input:** a directory. **Output:** a versioned binary snapshot.

Parses source into a language-agnostic AST representation stored in an
**arena** — a flat `std::vector` where nodes reference children by index range
rather than owning pointers. That representation is deliberate: cache-local
traversal, one amortised allocation instead of one per node, and no ownership
graph to get wrong.

The arena's index-range scheme depends on one invariant: **a node's children
are contiguous.** That is why the tree walk is breadth-first. A depth-first
walk would interleave grandchildren between siblings and silently break every
child lookup downstream.

All Tree-sitter handles are owned by RAII wrapper types following the rule of
zero — no destructor is written anywhere, because every member manages its own
lifetime.

### Tune Engine — analytics

**Input:** a snapshot. **Output:** a dependency graph and a metrics report.

Files become graph nodes. Edges run **from dependent to dependency** — if
`a.cpp` includes `b.hpp`, the edge is `a → b`. Every metric downstream depends
on that direction being right; reversing it inverts every instability figure
while leaving the numbers superficially plausible.

Import resolution is the accuracy ceiling of this layer and is treated as
such. RAAG has no compiler include path and no `sys.path` — it matches import
text against the file set it parsed, with tie-break rules. Every run reports
its resolved / ambiguous / external ratio, because a graph built from 26%
resolved imports describes something very different from one built from 90%,
and a caller cannot judge the metrics without knowing which they have.

### Master Engine — orchestration

**Input:** a snapshot, a graph, and a request. **Output:** a grounded
refactoring plan and an audit record.

This is where retrieval becomes *structural* rather than lexical:

1. **Blast radius** — traverse the graph from the target. Dependents (walking
   edges backwards) are what breaks; dependencies (walking forwards) are
   context. Both depth-bounded, both reported with truncation stated.
2. **Scoped retrieval** — vector search restricted to that file set.
   Structural reachability decides eligibility; semantic similarity only ranks
   within it.
3. **Context assembly** — the prompt states the target's metrics, names every
   dependent, and labels each retrieved chunk with its own coupling figures.
4. **Reasoning** — one LLM call, behind a protocol.
5. **Audit** — everything written to SQLite, including failures.

---

## Data flow

```
raag sample run <repo>
    └─► snapshots/repo.raag.bin        (schema v2)

raag tune run <snapshot>
    ├─► terminal report
    ├─► metrics/report.json            (--export-metrics)
    └─► graphs/dependencies.json       (--export-graph)

raag master index <snapshot>
    └─► Qdrant collection              (chunks + metrics payloads)

raag master refactor <file> "<request>"
    ├─► terminal output
    └─► audit.db                       (full request record)
```

Each arrow is a file or a service, never a shared in-memory object. That is
what makes the engines independently runnable — `raag tune` does not need the
C++ engine present, only its output.

---

## Cross-cutting decisions

### Protocols at every swap point

Both the embedding model and the reasoning backend sit behind `Protocol`
definitions with at least two implementations each — a real one and a free,
deterministic one.

This is not only about testability, though it makes the entire pipeline
verifiable in milliseconds without a network. It is also about *cost of
verification*: `--dry-run` prints the exact prompt that would be sent, for
free, which is how prompt assembly bugs get caught before they cost anything.

### Versioned contracts, rejected rather than best-effort

The snapshot format carries a schema version independent of the project
version. A reader encountering an unknown version **fails immediately**.

This is the most important small decision in the system. Best-effort parsing
of a changed layout usually *succeeds* — byte counts still work out, values
are still in range — and produces plausible nonsense that flows through the
graph into the metrics and out as a wrong architectural conclusion, with no
error anywhere to trace back to.

### Reporting confidence, not hiding it

Import resolution ratios, truncated blast radii, excluded context chunks, and
failed reasoning calls are all surfaced rather than swallowed. A tool that
conceals its own uncertainty overstates its accuracy, and the number a reader
most needs is often the one describing how much to trust the others.

### Thresholds are policy, not constants

Every threshold lives in configuration passed into the report builder, never
hard-coded in a metric function. Metrics compute facts; thresholds apply
judgement, and judgement varies by codebase.

Only error-severity violations fail a run. Warnings surface signals without
blocking, because a gate that fires on every soft signal gets disabled — and a
disabled gate protects nothing.

---

## What is deliberately not here

**Call-graph edges.** Resolving a callee to a definition requires overload
sets, virtual dispatch targets, and namespace lookup — none of which survive
in a parse tree. Emitting call edges from name matching alone would produce a
graph that looks richer and is measurably less correct.

**Semantic import resolution.** Correct resolution needs a compilation
database for C++ and a real module search for Python. This is on the roadmap
and is the single largest accuracy improvement available.

**Abstractness and distance from the main sequence.** Martin pairs instability
with abstractness (`D = |A + I − 1|`) precisely to distinguish an abstract
unstable interface layer from a concrete unstable tangle. RAAG's afferent gate
approximates the same intent more crudely.

**Incremental analysis.** Every run re-parses the whole repository. Content
hashing already exists at the chunk level; extending it to skip unchanged
files is what would make RAAG viable in a pre-commit hook rather than only in
CI.