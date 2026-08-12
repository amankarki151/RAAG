# Contributing to RAAG

Thank you for considering a contribution to RAAG.

RAAG is a multi-language monorepo spanning systems programming, graph theory,
and retrieval architecture. That breadth is deliberate — it means there is
meaningful work available whether your strength is low-level C++, algorithm
design, developer tooling, or technical writing.

This document covers how to set up the project, where the extension points are,
and what standards a contribution is held to.

---

## Table of Contents

- [Code of Conduct](#code-of-conduct)
- [Ways to Contribute](#ways-to-contribute)
- [Development Setup](#development-setup)
- [Repository Layout](#repository-layout)
- [Extension Points](#extension-points)
  - [Adding a Language Parser](#adding-a-language-parser)
  - [Adding a Quality Metric](#adding-a-quality-metric)
  - [Adding an Execution Engine](#adding-an-execution-engine)
- [Ideas Worth Building](#ideas-worth-building)
- [Coding Standards](#coding-standards)
- [Testing Requirements](#testing-requirements)
- [Commit Conventions](#commit-conventions)
- [Pull Request Process](#pull-request-process)
- [Architectural Review Gate](#architectural-review-gate)
- [Reporting Bugs](#reporting-bugs)
- [Proposing Large Changes](#proposing-large-changes)

---

## Code of Conduct

Be direct, be respectful, and critique code rather than people. Technical
disagreement is welcome and expected; hostility is not. Maintainers reserve the
right to close or lock threads that stop being productive.

---

## Ways to Contribute

Not every contribution is code:

| Contribution | Where to start |
| ------------ | -------------- |
| Bug fix | Open an issue first if the fix changes behavior |
| Language parser support | [Adding a Language Parser](#adding-a-language-parser) |
| New quality metric | [Adding a Quality Metric](#adding-a-quality-metric) |
| Performance improvement | Include before/after benchmark numbers |
| Documentation | Especially metric derivations and contract specs |
| Test coverage | Particularly edge cases in graph construction |
| Bug report | [Reporting Bugs](#reporting-bugs) |

---

## Development Setup

### Prerequisites

| Requirement | Version |
| ----------- | ------- |
| CMake | 3.28+ |
| C++ compiler | GCC 13+ / Clang 16+ |
| Python | 3.12 |
| Docker | Recent stable |
| Node.js | 20+ (only for the editor extension) |

### Setup

```bash
git clone <repository-url>
cd RAAG

# Build the C++ extraction layer
cmake -S sample_engine -B sample_engine/build -DCMAKE_BUILD_TYPE=Debug
cmake --build sample_engine/build --parallel

# Python environment
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt

# Vector store
docker compose up -d qdrant

# Credentials
cp .env.example .env
```

Verify your setup before making changes:

```bash
ctest --test-dir sample_engine/build      # C++ tests
pytest                                     # Python tests
```

If either suite fails on a clean checkout, that is a bug — please report it.

---

## Repository Layout

```
RAAG/
├── sample_engine/        # C++20 — extraction
├── tune_engine/          # Python — graph analytics
├── master_engine/        # Python — orchestration
├── cli/                  # Typer command-line interface
├── vscode-extension/     # TypeScript editor integration
└── docs/
    ├── ARCHITECTURE.md
    ├── CONTRACTS.md      # Binary snapshot schema — read before touching I/O
    └── METRICS.md        # Metric derivations
```

**Before modifying anything that crosses an engine boundary, read
`docs/CONTRACTS.md`.** The engines communicate through versioned data
contracts, and changing one without versioning it will break the others in ways
that are painful to debug.

---

## Extension Points

RAAG is designed so the three most common contributions do not require
understanding the whole system.

### Adding a Language Parser

Language support requires two pieces: grammar integration in the Sample Engine,
and edge-extraction rules in the Tune Engine. A parser without edge extraction
produces syntax trees that contribute nothing to the dependency graph.

**Sample Engine side:**

1. Register the Tree-sitter grammar in the build configuration.
2. Map the grammar's node types onto RAAG's internal AST node kinds. The
   internal representation is intentionally language-agnostic — resist the urge
   to leak language-specific node types downstream.
3. Add the file extensions to the traversal filter.

**Tune Engine side:**

4. Implement edge extraction for that language: what constitutes an import, an
   invocation, and an inheritance relationship. These differ meaningfully
   between languages and this is where most of the real work is.
5. Document the language's resolution limitations explicitly. Every language
   has constructs that syntactic parsing cannot resolve; say which ones.

**Required with the PR:**

- A small fixture repository in that language with a hand-verified expected
  dependency graph.
- Tests asserting the extracted graph matches the expected one.
- A note in `docs/` describing what the extractor does and does not resolve.

### Adding a Quality Metric

New metrics are welcome, but must be defensible rather than invented.

**Requirements:**

1. **Cite the source.** The metric should come from published software
   engineering literature, or be a clearly-documented derivation from one that
   is. Novel heuristics are acceptable but must be labeled as such.
2. **Document the formula** in `docs/METRICS.md`, including edge cases —
   division by zero, isolated nodes, and self-referential edges are the usual
   suspects.
3. **State the interpretation.** A number with no guidance on what constitutes
   a good or bad value is not actionable. Include the range, and be explicit
   about when a "bad" value is legitimate.
4. **Test against hand-calculated examples.** Not just "it runs" — a fixture
   graph whose metric value you computed manually and asserted against.

**Implementation notes:** metrics live in the Tune Engine and operate on the
constructed graph. They should be pure functions of the graph — no I/O, no
global state — which keeps them trivially testable and composable.

### Adding an Execution Engine

The largest category of contribution. A new engine is justified when a workload
does not belong in any existing one — for example, a runtime tracing layer, or
a persistence layer for historical metric trends.

**Before writing code, open a proposal issue.** A new engine changes the
platform's architecture and the maintainers need to agree on its boundaries
first.

**A new engine must:**

1. Own exactly one responsibility, expressible in one sentence.
2. Communicate through a **versioned contract**, never shared mutable state or
   direct imports across engine boundaries.
3. Be independently testable — its test suite must pass without the other
   engines running.
4. Be independently buildable, with its own build configuration.
5. Document its contract in `docs/CONTRACTS.md` with an explicit schema
   version.

Engines are decoupled on purpose. A contribution that couples two engines
directly will be asked to introduce a contract instead, even if the coupled
version is shorter.

---

## Ideas Worth Building

Beyond the three extension points, the following would meaningfully improve the
platform. This list is not exhaustive — a well-reasoned proposal for something
not listed here is welcome.

### Analysis Depth

- **Abstractness metric and main-sequence distance.** Instability alone
  penalizes modules that are intentionally volatile. Pairing it with
  abstractness (`A`) and computing `D = |A + I − 1|` distinguishes a badly
  coupled module from a correctly designed adapter. This is the single highest
  value analytical addition.
- **Semantic call-graph resolution.** Current edges are syntactic. Integrating
  a compilation database would resolve virtual dispatch, template
  instantiation, and macro expansion correctly.
- **Cyclomatic and cognitive complexity** at function granularity, to
  complement module-level coupling.
- **Historical trend analysis.** Metrics computed against Git history reveal
  whether a module is decaying or improving — often more actionable than a
  point-in-time snapshot.
- **Cycle-breaking suggestions.** Given a detected dependency cycle, identify
  the minimum set of edges whose removal would break it.
- **Dead code and unreachable module detection** via graph reachability from
  declared entry points.

### Performance and Scale

- **Incremental re-analysis.** Re-parsing an entire repository when three files
  changed is wasteful. Caching AST snapshots keyed by content hash and
  re-parsing only dirty subtrees would make RAAG viable in pre-commit hooks.
- **Memory-mapped snapshot reading** for repositories whose AST data exceeds
  comfortable memory limits.
- **Parallel graph construction** — the Tune Engine is currently the
  single-threaded stage in an otherwise parallel pipeline.
- **Streaming serialization**, so extraction and analysis can overlap rather
  than running strictly sequentially.

### Retrieval and Reasoning Quality

- **Adaptive blast-radius depth.** A fixed traversal depth is crude; depth
  could adapt to graph density around the target.
- **Retrieval evaluation harness.** A benchmark measuring whether the retrieved
  context actually contains the files a change needed to touch — the honest way
  to validate that graph filtering beats naive similarity.
- **Edge-weight-aware traversal.** Not all dependencies are equally strong; an
  inheritance edge implies tighter coupling than a single function call.
- **Multi-target blast radius**, for evaluating a proposed change spanning
  several modules at once.

### Developer Experience

- **Interactive dependency graph visualization** in the editor extension.
- **Pre-commit hook integration**, so violations surface before push rather
  than in CI.
- **Metric diff reporting** — showing how a branch changes architectural health
  relative to its base, rather than reporting absolute values.
- **Configurable per-layer thresholds** via a config file, so a plugin
  directory and a core directory can be held to different standards.
- **Export formats** — Graphviz, Mermaid, JSON — so the graph is consumable by
  other tooling.
- **Baseline and ratcheting support**, allowing existing violations to be
  grandfathered while preventing new ones. This is what makes adoption on an
  existing legacy codebase realistic.

### Integration

- Support for CI platforms beyond GitHub Actions.
- A language server implementation, so the analysis is editor-agnostic.
- Machine-readable report output for downstream tooling.

---

## Coding Standards

### C++ (Sample Engine)

- **C++20.** Prefer standard library facilities over hand-rolled equivalents.
- **RAII is non-negotiable.** No raw `new`/`delete` in application code. Any
  C API handle gets an owning wrapper with a custom deleter.
- **No raw owning pointers.** Use `std::unique_ptr` by default; `std::shared_ptr`
  only when ownership is genuinely shared, and justify it in review.
- **Const-correctness** throughout.
- **Prefer value semantics.** Return by value and let the compiler elide.
- Follow the existing file's formatting; a `.clang-format` config is provided
  and CI checks it.

### Python (Tune and Master Engines)

- **Strict typing.** All function signatures annotated; `mypy` runs in CI and
  must pass.
- **No bare `except:`.** Catch specific exceptions.
- **Pure functions where possible**, particularly for metrics — they should be
  functions of the graph and nothing else.
- **Dataclasses over dicts** for structured data crossing module boundaries.
- Formatting and linting via the provided `ruff` configuration.

### TypeScript (Editor Extension)

- `strict` mode enabled; no implicit `any`.
- No unhandled promise rejections.

### Universal

- **Names describe intent, not implementation.** `blast_radius` rather than
  `bfs_result`.
- **Comment the why, not the what.** If a line needs a comment explaining what
  it does, rewrite the line.
- **No commented-out code.** Version control exists.

---

## Testing Requirements

Every behavioral change requires a test. Specifically:

| Change type | Required |
| ----------- | -------- |
| Bug fix | A test that fails before the fix and passes after |
| New metric | Assertions against hand-calculated fixture values |
| New parser | Fixture repository with a verified expected graph |
| New engine | An independent suite that passes in isolation |
| Performance work | Benchmark numbers, before and after, same machine |

Tests should assert on behavior rather than implementation details. A test that
breaks when you rename a private method is a liability.

---

## Commit Conventions

RAAG follows [Conventional Commits](https://www.conventionalcommits.org/).

```
<type>(<scope>): <subject>

<body — the why, not the what>

<footer — issue references, breaking change notes>
```

**Types:** `feat`, `fix`, `perf`, `refactor`, `test`, `docs`, `build`, `ci`,
`chore`

**Scopes:** `sample`, `tune`, `master`, `cli`, `extension`, `docs`, `ci`

**Examples:**

```
feat(tune): add LCOM4 cohesion metric

Implements the connected-components variant of LCOM, which handles
the accessor-method false positive that LCOM1 produces on data classes.

Closes #142
```

```
fix(sample): prevent handle leak when parser initialization fails

The tree handle was acquired before the null check on the parser,
so a failed initialization leaked it. Moved acquisition after
validation and wrapped it in the existing RAII type.
```

**Breaking changes** to any inter-engine contract must include a
`BREAKING CHANGE:` footer describing the migration.

---

## Pull Request Process

1. **Fork and branch.** Name branches descriptively: `feat/lcom4-metric`.
2. **Keep it focused.** One logical change per PR. A PR that fixes a bug *and*
   refactors a module is two PRs.
3. **Update documentation** in the same PR as the code. Docs updated later are
   docs updated never.
4. **Update `CHANGELOG.md`** under `[Unreleased]`, in the same commit as the
   change.
5. **Ensure CI passes**, including the architectural gate described below.
6. **Write a description that explains the why.** The diff already shows what
   changed.

Maintainers review for correctness, architectural fit, and test adequacy.
Expect questions — they are about the code, not about you.

---

## Architectural Review Gate

RAAG analyzes its own pull requests.

The CI pipeline recomputes coupling metrics for every touched module. If a
change pushes a core module's instability above the configured threshold, the
check fails and a report is posted to the PR identifying which module regressed
and why.

This is not a formality. If your PR trips the gate, the options are:

- **Restructure the change** to avoid the added coupling — usually the right
  answer.
- **Argue that the threshold is wrong** for that module, with reasoning. This
  is a legitimate outcome and has resulted in threshold changes before.

What is not acceptable is disabling the check to merge.

---

## Reporting Bugs

A good bug report includes:

- **Environment:** OS, compiler version, Python version.
- **The target repository** the analysis was run against, or a minimal
  reproduction if the original cannot be shared.
- **The exact command** invoked.
- **Expected versus actual behavior.**
- **Full error output**, not a paraphrase.

For incorrect analysis results specifically — a wrong edge, a wrong metric
value — include the source construct that produced it. "The graph is wrong" is
not actionable; "an inheritance edge is missing for this class declaration" is.

---

## Proposing Large Changes

Open an issue before writing code if your change:

- Introduces a new engine
- Modifies an inter-engine contract
- Changes a metric's formula or interpretation
- Alters the CLI surface
- Adds a significant dependency

A short proposal covering the problem, the approach, and the alternatives you
considered saves everyone the cost of a rejected implementation. Maintainers
would much rather discuss a paragraph than close a thousand-line PR.

---

Thank you for contributing.