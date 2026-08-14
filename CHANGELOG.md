# Changelog

All notable changes to RAAG (AI-Powered Architectural Analytics Platform) are
documented in this file.

The format is based on [Keep a Changelog v1.0.0](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

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


#### Tune Engine (Python 3.12 — Quality Analytics Layer)
- Binary snapshot reader with cross-engine schema version validation.
- Dependency edge extraction for imports, invocations, and inheritance.
- Directed graph construction over repository entities using NetworkX.
- Circular dependency detection and topological ordering reports.
- Afferent Coupling (Ca) and Efferent Coupling (Ce) computation per module.
- Instability index `I = Ce / (Ca + Ce)` with explicit zero-degree handling.
- Lack of Cohesion of Methods (LCOM) analysis for Single Responsibility
  Principle violation detection.
- Structured metrics report with configurable violation thresholds.
- Binary snapshot reader with schema version validation and defensive
  bounds checking on all length-prefixed fields.
  - Import resolution against the parsed file set, with explicit reporting of
  resolved, ambiguous, and external outcomes.
- Dependency graph construction over NetworkX, with parallel edges collapsed
  to a single weighted edge.
- Cycle detection via strongly connected components, and dependency layering
  over the condensation so cyclic repositories still produce a usable ordering.

#### Master Engine (Python 3.12 — Orchestration Layer)
- AST-boundary-aware code chunking at function and class granularity.
- Vector embedding pipeline with structural metadata payloads.
- Qdrant collection management and metadata-filtered similarity search.
- Blast radius computation over the dependency graph with bounded traversal
  depth.
- Context assembly injecting exact coupling metrics alongside retrieved source.
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
- Inter-engine binary contract specification (docs/CONTRACTS.md).
- Metric derivation reference.

---

## Release History

<!--
No releases yet. The first tagged release will be cut once the full pipeline
executes end to end against a real repository.

When cutting a release:
  1. Move the relevant entries out of [Unreleased] into a new version heading.
  2. Date it in ISO 8601 format (YYYY-MM-DD).
  3. Add the comparison link at the bottom of this file.
  4. Tag the commit to match: `git tag -a v0.1.0 -m "..."`.

Version heading format:

## [0.1.0] - YYYY-MM-DD

### Added
- ...
-->

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

<!--
Comparison links — add one per release as versions are tagged.

[Unreleased]: https://github.com/<user>/<repo>/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/<user>/<repo>/releases/tag/v0.1.0
-->
