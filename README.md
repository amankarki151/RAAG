<div align="center">

# RAAG

**AI-Powered Architectural Analytics Platform**

*Repository-scale dependency analysis, quantified architectural metrics, and blast-radius-scoped refactoring intelligence.*

[![C++](https://img.shields.io/badge/C%2B%2B-20-00599C?logo=cplusplus&logoColor=white)](https://isocpp.org/)
[![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![CMake](https://img.shields.io/badge/CMake-3.28%2B-064F8C?logo=cmake&logoColor=white)](https://cmake.org/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

</div>

---

## Table of Contents

- [Writing](#writing)
- [Overview](#overview)
- [The Problem](#the-problem)
- [How RAAG Works](#how-raag-works)
- [Architecture](#architecture)
- [Engines](#engines)
- [Metrics Reference](#metrics-reference)
- [Project Status](#project-status)
- [Getting Started](#getting-started)
- [Usage](#usage)
- [CI/CD Integration](#cicd-integration)
- [Repository Layout](#repository-layout)
- [Design Decisions](#design-decisions)
- [Known Limitations](#known-limitations)
- [Roadmap](#roadmap)
- [Contributing](#contributing)
- [License](#license)

---

## Writing

- [Building a Parallel C++ Source Parser: jthread, stop_token, and the Deadlock I Didn't See Coming](https://amankarki.hashnode.dev/parallel-cpp-source-parser-jthread-stop-token) — *Hashnode (also on Medium)*

---

## Overview

RAAG analyzes the structure of large codebases and turns architecture into
something measurable rather than something you argue about in review.

It parses a repository at native speed, builds a directed dependency graph of
every file, class, and function, computes industry-standard coupling and
cohesion metrics across that graph, and uses the graph — not text similarity —
to scope AI-assisted refactoring to exactly the files a change can reach.

Three engines, three languages, one contract-driven pipeline:

| Engine | Stack | Responsibility |
| ------ | ----- | -------------- |
| **Sample** | C++20 | Parallel filesystem traversal and syntax tree extraction |
| **Tune** | Python 3.12 | Dependency graph construction and metric computation |
| **Master** | Python 3.12 | Blast-radius-scoped retrieval and refactoring analysis |

---

## The Problem

Past a few hundred thousand lines, a codebase's architecture stops being a
decision and starts being an accident. Boundaries erode, coupling accumulates,
and dependency cycles appear that nobody deliberately introduced.

Existing tooling doesn't close the gap:

- **Linters and static analyzers** work at the file or function level. They have
  no model of cross-module structural risk — a file can be perfectly clean and
  still be the most dangerous module in the repository.
- **Dependency visualizers** render a graph but rarely quantify it. A picture of
  a hairball tells you it's a hairball; it doesn't tell you where to cut.
- **General-purpose AI assistants** reason well locally but cannot hold a
  multi-million-line repository in context. Their suggestions are not
  blast-radius aware, so a locally correct fix can break a distant module.

RAAG treats architecture as a queryable graph with quantified properties, then
constrains AI reasoning to a structurally-derived subset of the codebase.

---

## How RAAG Works

```
  Source Repository
         │
         ▼
  ┌──────────────────┐
  │  SAMPLE ENGINE   │   Parallel parse via std::jthread pool
  │      (C++20)     │   Tree-sitter → internal AST representation
  └────────┬─────────┘
           │  versioned binary snapshot
           ▼
  ┌──────────────────┐
  │   TUNE ENGINE    │   Dependency graph construction
  │   (Python 3.12)  │   Ca / Ce / Instability / LCOM
  └────────┬─────────┘
           │  dependency graph + metrics report
           ▼
  ┌──────────────────┐
  │  MASTER ENGINE   │   Blast radius → filtered vector retrieval
  │   (Python 3.12)  │   Context assembly → AI reasoning → audit log
  └────────┬─────────┘
           │
           ▼
  Refactoring Plan + Audit Trail
```

Each stage communicates through a **versioned data contract**, never shared
memory. Any engine can be replaced, benchmarked, or tested in isolation.

---

## Architecture

## Results

Benchmarked on 579 source files from open-source C++ repositories (Release build, 8 cores):

| | Time | Throughput |
|---|---|---|
| Single-threaded | 1.657 s | 349.4 files/s |
| Parallel | 0.449 s | 1290.2 files/s |

**3.69x speedup.** 1,099,446 AST nodes extracted, zero parse failures, serialized to a 27.6 MB versioned binary snapshot.

### Design Constraints

RAAG was built against four constraints that shaped every subsequent decision:

1. **Parsing must not be the bottleneck.** Interpreted parsers over
   million-line repositories produce unacceptable wall-clock times, which is
   why extraction lives in C++ rather than in the analytics layer.
2. **Architectural health must be quantified, not asserted.** Every claim RAAG
   makes about a module reduces to a number with a published formula.
3. **AI context must be bounded by structure.** The reasoning layer is never
   handed an unbounded view of the repository.
4. **Every automated decision must be reconstructible.** If RAAG suggests a
   refactor, you can retrieve the exact inputs that produced it.

### Language Boundaries

The split is deliberate rather than incidental. C++20 handles the workload that
is I/O and CPU bound with no need for ecosystem breadth. Python handles graph
algorithms and orchestration, where library maturity and iteration speed matter
more than raw throughput. The cost is an inter-process contract; the benefit is
that each engine is written in the language its problem actually calls for.

---

## Engines

### Sample Engine — Extraction Layer

**Stack:** Modern C++20 · Tree-sitter C API · CMake · Pybind11

Recursively enumerates a target repository and parses every supported source
file into a normalized syntax tree.

- **Concurrency** — a `std::jthread` worker pool distributes files across cores.
  Cancellation is cooperative via `std::stop_token`, so an interrupted run
  terminates cleanly with no orphaned work or detached threads.
- **Resource safety** — all Tree-sitter parser and tree handles are owned by
  RAII wrapper types. No raw pointer is manually released anywhere in the
  application layer, and cleanup remains correct under exception unwinding.
- **Representation** — Tree-sitter's native trees are normalized into an
  internal, language-agnostic AST capturing node kind, symbol name, byte range,
  and child relationships. Downstream engines never depend on Tree-sitter's
  format directly.
- **Output** — a versioned, binary-packed snapshot. The schema carries its own
  version field, independent of the platform version.

### Tune Engine — Quality Analytics Layer

**Stack:** Python 3.12 (strict typing, asyncio) · NetworkX

Converts structural data into a graph and measures it.

- **Graph construction** — files, classes, and functions become nodes; imports,
  invocations, and inheritance become typed directed edges.
- **Topology analysis** — circular dependency detection, topological ordering,
  and shortest-path queries between arbitrary modules.
- **Metrics** — afferent and efferent coupling, instability, and cohesion
  (see [Metrics Reference](#metrics-reference)).
- **Rule enforcement** — LCOM-derived Single Responsibility Principle checks,
  and static RAII pattern verification for C++ sources.

### Master Engine — Orchestration Layer

**Stack:** Python 3.12 · Qdrant · SQLite · Anthropic API (Claude Sonnet 5, configurable)

Where graph analytics constrains generative reasoning.

- **Chunking** — code is segmented at AST boundaries (function and class level)
  rather than by line windows, so no retrieved chunk splits a logical unit.
- **Blast radius** — before any retrieval, the dependency graph is traversed
  from the target node to a bounded depth. The resulting node set defines
  everything a change can reach.
- **Filtered retrieval** — vector search is constrained to blast-radius nodes.
  Structural relevance gates semantic relevance, not the reverse.
- **Context assembly** — the assembled prompt carries the target source, its
  exact Ca/Ce/I values, and each retrieved chunk labeled with its own metrics.
- **Reasoning** — the assembled context is sent to Claude Sonnet 5 by default,
  configurable via `RAAG_MODEL` to escalate to Opus 5 for higher-stakes or
  more ambiguous requests. Kept behind a `Reasoner` protocol so the model —
  or provider — is a configuration choice, not a code change. A `--dry-run`
  mode assembles and prints the full prompt without calling the API, which is
  how prompt assembly gets verified before it costs anything.
- **Audit trail** — every request writes target node, full blast-radius node
  list, metrics snapshot, assembled prompt, and returned plan to SQLite.

---

## Metrics Reference

| Metric | Definition | Interpretation |
| ------ | ---------- | -------------- |
| **Afferent Coupling (Ca)** | Count of incoming dependencies | High Ca means many modules depend on this one. Changes here are expensive. |
| **Efferent Coupling (Ce)** | Count of outgoing dependencies | High Ce means this module depends on many others. It is fragile to upstream change. |
| **Instability (I)** | `I = Ce / (Ca + Ce)` | Ranges 0.0–1.0. `0` is maximally stable, `1` maximally unstable. |
| **LCOM** | Lack of Cohesion of Methods | High values indicate a class whose methods operate on disjoint field sets — a Single Responsibility Principle signal. |

**On interpreting instability:** a high `I` is not automatically a defect. An
adapter or plugin layer *should* be unstable — that is its job. The signal RAAG
looks for is a module that is both foundational (high Ca) and unstable, or a
core module whose instability is rising over time.

---

## Project Status

RAAG is under active development. This table reflects actual implementation
state, not planned scope.

| Component | Status |
| --------- | ------ |
| Sample Engine — AST parsing | ✅ Complete |
| Sample Engine — concurrency & serialization | ✅ Complete |
| Sample ↔ Tune binary contract | ✅ Complete |
| Tune Engine — dependency graph | ✅ Complete |
| Tune Engine — metrics | ✅ Complete |
| Master Engine — vector store | ✅ Complete |
| Master Engine — GraphRAG pipeline | 📋 Planned |
| CLI | 📋 Planned |
| CI/CD guardrail | 📋 Planned |
| Editor extension | 📋 Planned |

Legend: ✅ Complete · 🔧 In progress · 📋 Planned

---

## Getting Started

### Prerequisites

| Requirement | Version |
| ----------- | ------- |
| CMake | 3.28+ |
| C++ compiler | GCC 13+ / Clang 16+ |
| Python | 3.12 |
| Docker | Any recent version (for the vector store) |

### Installation

```bash
# Clone the repository
git clone <repository-url>
cd RAAG

# Build the Sample Engine
cmake -S sample_engine -B sample_engine/build -DCMAKE_BUILD_TYPE=Release
cmake --build sample_engine/build --parallel

# Set up the Python environment
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Start the vector store
docker compose up -d qdrant

# Configure credentials
cp .env.example .env
# Edit .env with your API key
```

---

## Usage

### Extract syntax trees

```bash
raag sample /path/to/repository
```

Traverses the target repository in parallel and writes a binary AST snapshot.

### Analyze architecture

```bash
raag tune /path/to/repository
```

Builds the dependency graph and emits a metrics report: per-module coupling,
instability, cohesion, and any threshold violations, sorted by severity.

### Request a refactoring analysis

```bash
raag master refactor --target <module>
```

Computes the blast radius for the target, retrieves structurally relevant
context, and returns a grounded refactoring plan. Every invocation is logged.

---

## CI/CD Integration

RAAG runs headless in continuous integration as an architectural guardrail.

On each pull request the workflow recomputes coupling metrics for every touched
module. If a change pushes a core module's instability above the configured
threshold, the check fails and a remediation suggestion is posted directly to
the pull request.

```yaml
# .github/workflows/architecture.yml
- name: Architectural analysis
  run: raag tune . --fail-on-instability 0.4 --scope core
```

This converts architectural review from a manual, reactive process into an
automated gate that runs before a human ever opens the diff.

---

## Repository Layout

```
RAAG/
├── sample_engine/          # C++20 extraction layer
│   ├── include/raag/       # Public headers
│   ├── src/                # Implementation
│   ├── bindings/           # Pybind11 module
│   └── CMakeLists.txt
├── tune_engine/            # Python graph analytics
│   ├── raag_tune/
│   └── tests/
├── master_engine/          # Python orchestration
│   ├── raag_master/
│   └── tests/
├── cli/                    # Typer command-line interface
├── vscode-extension/       # TypeScript editor integration
├── docs/                   # Specifications and contracts
│   ├── ARCHITECTURE.md
│   ├── CONTRACTS.md        # Binary snapshot schema
│   └── METRICS.md          # Metric derivations
├── .github/workflows/
├── docker-compose.yml
├── CHANGELOG.md
└── README.md
```

---

## Design Decisions

**Why a custom binary format instead of Protobuf or FlatBuffers?**
The Sample↔Tune contract has exactly one producer and one consumer, both
in-repo. A hand-rolled length-prefixed format removes a dependency and a code
generation step from the build, at the cost of writing the serializer. The
schema carries an explicit version field so drift is caught at load time rather
than surfacing as corrupt data downstream.

**Why `std::jthread` rather than a thread pool library?**
Cooperative cancellation via `std::stop_token` is the property that matters
here: a parse run over a large repository must be interruptible without leaking
threads or leaving partial output. Automatic joining on destruction removes an
entire class of lifetime bug from the worker pool.

**Why is retrieval graph-filtered rather than purely semantic?**
Vector similarity measures whether two pieces of code *read* similarly. It does
not measure whether one can break the other. For refactoring, structural
reachability is the correct relevance signal, and semantic similarity is a
ranking function applied within it.

**Why log every AI request in full?**
A refactoring suggestion that cannot be explained cannot be trusted with
production architecture. Storing the blast radius and metrics snapshot
alongside the prompt makes each suggestion reconstructible and reviewable after
the fact.

---

## Known Limitations

These are documented deliberately — accurate scoping is part of the engineering.

- **Call-graph resolution is syntactic.** Edges are derived from Tree-sitter
  parse trees. Virtual dispatch, heavy template metaprogramming, and
  macro-expanded code are resolved approximately. Exact resolution requires a
  semantic compilation database, which is on the roadmap.
- **A single global instability threshold is a blunt instrument.** It penalizes
  modules that are intentionally volatile. Pairing instability with an
  abstractness metric and distance-from-main-sequence scoring is the correct
  fix and is planned.
- **Language coverage is limited to available Tree-sitter grammars** and to the
  edge-extraction rules implemented per language.
- **Import resolution is a suffix match, not a semantic lookup.** RAAG has no
  compiler include path or `sys.path`; it matches import text against the file
  set it parsed, with tie-break rules where several files could answer. Every
  run reports its resolved / ambiguous / external ratio so the graph's
  reliability is visible rather than assumed. Semantic resolution via a
  compilation database is the correct fix and is on the roadmap.
- **Call edges are not extracted.** Resolving a callee to a definition needs
  overload sets, virtual dispatch targets, and namespace lookup, none of which
  survive in a parse tree. Emitting call edges from name matching alone would
  produce a graph that looks richer and is measurably less correct.

---

## Roadmap

- Semantic call-graph resolution via compilation database integration
- Abstractness metric and distance-from-main-sequence scoring
- Per-layer configurable instability thresholds
- Incremental re-analysis (re-parse only changed subtrees)
- Expanded language support

---

## Contributing

Contributions are welcome. Please read `CONTRIBUTING.md` before opening a pull
request, and note that RAAG runs its own architectural guardrail on incoming
changes — a pull request that raises core module instability past threshold
will be flagged automatically.

---

## License

Released under the MIT License. See [LICENSE](LICENSE) for details.
