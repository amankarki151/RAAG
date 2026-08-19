# Changelog

All notable changes to RAAG (AI-Powered Architectural Analytics Platform) are
documented in this file.

The format is based on [Keep a Changelog v1.0.0](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Added

#### Developer Experience
- Editor extension surfacing module instability as inline decorations.

### Documentation
- Technical specification covering architecture, engine internals, metric
  definitions, and design rationale.

---

## [1.0.0] - 2026-08-19

First public release. Parse a repository, quantify its architecture, retrieve
code by structural relevance, and get AI-assisted refactoring suggestions
scoped to exactly what a change can reach — end to end, behind one CLI,
gated by CI.


## [0.4.0] - 2026-08-16

Containerized and CI-gated: RAAG now analyses its own source on every pull
request, blocking merges that push a core module's instability past
threshold.

### Added

#### Infrastructure
- Two-stage Dockerfile: the Sample Engine compiles in a full build image,
  and only the resulting binary is copied into a slim Python runtime.
- `docker-compose.yml` wiring the `raag` service to Qdrant by service name,
  never `localhost`, so it runs identically outside a developer's machine.
- GitHub Actions workflow analysing RAAG's own source on every pull request
  and push to `main`, posting or updating a single PR comment per thread
  rather than one per push, and uploading the full metrics report as a
  build artifact.
- `scripts/format_pr_comment.py` rendering `--export-metrics` JSON output
  as a Markdown PR comment.

### Changed
- Instability threshold for the self-analysis gate calibrated to 0.8, above
  the CLI default of 0.4. RAAG's own orchestration layer legitimately has
  moderate instability by design — a composition root is supposed to
  depend on many collaborators. This is the exact gap documented in
  `docs/METRICS.md`: instability alone cannot distinguish a composition
  root from a genuinely fragile module without a paired abstractness
  figure.

### Documentation
- `requirements.txt` and `pyproject.toml`'s dependency list were two
  separate sources of truth that could silently drift; consolidated
  dependency declarations into `pyproject.toml` after the drift caused
  `pip install -e .` to fail in both Docker and CI while working locally.

---

## [0.3.0] - 2026-08-16

Three engines, one command surface: `raag sample`, `raag tune`, `raag master`.

### Added

#### Developer Experience
- Unified `raag` CLI via Typer, replacing three separate module invocations.
- Rich terminal rendering, instability colour-banded to the same thresholds
  the violation checks use, so colour and verdict never disagree.
- `--fail-on-instability` on `raag tune run` — the exit-code contract Day 9's
  CI gate depends on: 0 clean, 1 violations found, 2 could not run.
- Failure messages that carry their own fix: a missing Sample Engine binary
  prints the build command; an unreachable Qdrant prints the start command,
  checked before any vector operation.

### Changed
- Removed the provisional `python -m raag_tune` / `python -m raag_master`
  entry points, superseded by the unified CLI. This alone recovered most of
  a 71%-to-86% coverage jump — 294 statements of dead code had been counted
  as untested.

### Documentation
- `.clang-format` and `docs/ARCHITECTURE.md`, both referenced by
  `CONTRIBUTING.md` since the project was scaffolded but never created
  until now.

---

## [0.2.0] - 2026-08-15

Full pipeline end to end: a refactoring request scoped by dependency
graph, retrieved with structural filtering, assembled with injected
metrics, reasoned over by an LLM, and logged for audit.

### Added

#### Master Engine (Python 3.12 — Orchestration Layer)
- AST-boundary-aware code chunking at function and class granularity,
  with content-hashed identity so re-indexing overwrites rather than
  duplicates.
- Pluggable embedding layer behind an `Embedder` protocol, with a
  semantic fastembed implementation and a deterministic offline
  fallback for testing.
- Qdrant collection management with coupling metrics attached to every
  chunk's payload at index time.
- Metadata-filtered similarity search, restricting retrieval to a
  computed set of files rather than the whole repository.
- Blast radius computation over the dependency graph, walking dependents
  and dependencies separately and bounding traversal by depth.
- Context assembly injecting exact Ca/Ce/I values alongside every
  retrieved chunk, with an explicit character budget and truncation
  reported rather than silently applied.
- Pluggable reasoning backend behind a `Reasoner` protocol: an Anthropic
  API implementation and a free dry-run mode that assembles and prints
  the prompt without calling out.
- SQLite audit log capturing target, full blast radius, metrics
  snapshot, assembled prompt, and response — including failed requests
  — for every reasoning call.
- Progress reporting during embedding via tqdm.

---

## [0.1.0] - 2026-08-14

First end-to-end usable slice: parse a repository, build its dependency
graph, compute coupling and cohesion metrics.

### Added

#### Sample Engine (C++20 — Extraction Layer)
- Recursive filesystem traversal over target repositories via `std::filesystem`.
- Tree-sitter integration for Concrete and Abstract Syntax Tree generation.
- RAII wrapper types owning all parser and tree handles, guaranteeing
  deterministic cleanup under exception unwinding.
- Internal language-agnostic AST representation decoupled from Tree-sitter's
  native tree format.
- `std::jthread` worker pool with cooperative cancellation via `std::stop_token`.
- Versioned binary snapshot serialization for downstream consumption.
- Import target and inheritance base-type extraction, without which the
  snapshot recorded that a file had imports but not what they referenced.
- Member field-access extraction (`self.x`, `this->x`), the data cohesion
  analysis is computed from.

#### Tune Engine (Python 3.12 — Quality Analytics Layer)
- Binary snapshot reader with cross-engine schema version validation and
  defensive bounds checking on all length-prefixed fields.
- Dependency edge extraction for imports and inheritance, with import
  resolution reported as resolved, ambiguous, or external rather than
  asserted as certain.
- Directed graph construction over repository files using NetworkX, with
  parallel edges collapsed to a single weighted edge.
- Circular dependency detection via strongly connected components, and
  dependency layering over the condensation so cyclic repositories still
  produce a usable ordering.
- Afferent Coupling (Ca) and Efferent Coupling (Ce) computation per module.
- Instability index `I = Ce / (Ca + Ce)`, with violation checks gated on a
  minimum afferent coupling so leaf modules are not flagged for being
  correctly unstable.
- LCOM1 and LCOM4 cohesion analysis, with LCOM4 as the primary figure and
  its method groupings reported alongside the score.
- Structured metrics report with configurable violation thresholds and
  JSON export.

### Changed
- Snapshot schema version 2: added the `FieldAccess` node kind. Existing
  snapshots must be regenerated.

### Documentation
- Inter-engine binary contract specification (`docs/CONTRACTS.md`).
- Metric derivation reference (`docs/METRICS.md`).

---

## Conventions

### Change Categories

Entries are grouped under the following headings, in this order:

| Category       | Use for                                                    |
| -------------- | ---------------------------------------------------------- |
| `Added`        | New features                                                |
| `Changed`      | Changes to existing functionality                           |
| `Deprecated`   | Features slated for removal in an upcoming release          |
| `Removed`      | Features removed in this release                            |
| `Fixed`        | Bug fixes                                                   |
| `Security`     | Vulnerability remediation                                   |

### Versioning Policy

Given a version number `MAJOR.MINOR.PATCH`:

- **MAJOR** — incompatible changes to a public interface. For this project that
  includes the binary snapshot schema, the CLI command surface, and the metrics
  report format, since downstream consumers depend on all three.
- **MINOR** — functionality added in a backward-compatible manner.
- **PATCH** — backward-compatible bug fixes.

Pre-1.0.0 releases carry no backward-compatibility guarantee; the public
interface is considered unstable until `1.0.0`.

### Binary Schema Versioning

The Sample Engine's snapshot format carries an independent schema version.
A schema change is a breaking change to the Sample↔Tune contract and must be
recorded here under `Changed` with an explicit migration note, even when the
platform version bump is only a MINOR increment.

---

[Unreleased]: https://github.com/amankarki151/RAAG/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/amankarki151/RAAG/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/amankarki151/RAAG/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/amankarki151/RAAG/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/amankarki151/RAAG/releases/tag/v0.1.0
