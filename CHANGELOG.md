# Changelog

All notable changes to RAAG (AI-Powered Architectural Analytics Platform) are
documented in this file.

The format is based on [Keep a Changelog v1.0.0](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]
 
### Added
 
#### Master Engine (Python 3.12 — Orchestration Layer)
- AST-boundary-aware code chunking at function and class granularity, with
  size floors and ceilings tuned for retrieval quality rather than storage
  efficiency.
- Pluggable embedding layer behind an `Embedder` protocol, with a semantic
  fastembed implementation and a deterministic offline fallback for testing.
- Qdrant collection management with coupling metrics attached to every
  chunk's payload at index time.
- Metadata-filtered similarity search, restricting retrieval to a given set
  of files — the mechanism blast-radius scoping will drive.
- Progress reporting during embedding via tqdm, since semantic embedding on a
  full repository is slow enough on CPU that a silent wait is indistinguishable
  from a hang.
- Blast radius computation over the dependency graph with bounded traversal
  depth.
- Context assembly injecting exact coupling metrics alongside retrieved
  source.
- Structurally-grounded refactoring analysis via AI reasoning backend.
- SQLite audit log capturing target node, blast radius, metrics snapshot,
  assembled prompt, and returned plan for every request.
  
#### Developer Experience
- `raag sample`, `raag tune`, and `raag master refactor` commands via Typer.
- Styled terminal reporting and progress indication via Rich.
- Editor extension surfacing module instability as inline decorations.
#### Infrastructure
- Monorepo layout with independently buildable engine boundaries.
- CMake build configuration for the C++ extraction layer.
- Container definitions and service composition for local orchestration.
- Continuous integration workflow enforcing instability thresholds on pull
  requests, with automated remediation suggestions.
### Documentation
- Technical specification covering architecture, engine internals, metric
  definitions, and design rationale.
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

[Unreleased]: https://github.com/amankarki151/RAAG/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/amankarki151/RAAG/releases/tag/v0.1.0

