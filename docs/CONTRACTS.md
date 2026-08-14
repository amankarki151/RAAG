# Inter-Engine Contracts

RAAG's engines are separate processes written in different languages. They
communicate through versioned data formats rather than shared memory or direct
calls. This document specifies those formats.

**Read this before changing anything that crosses an engine boundary.** A
change to a layout here is a breaking change even when it looks like an
implementation detail on one side.

---

## Table of Contents

- [Why contracts rather than coupling](#why-contracts-rather-than-coupling)
- [AST Snapshot Format v1](#ast-snapshot-format-v1)
  - [File layout](#file-layout)
  - [Field reference](#field-reference)
  - [Node kind values](#node-kind-values)
  - [Structural invariants](#structural-invariants)
- [Schema versioning](#schema-versioning)
- [Changing the format](#changing-the-format)

---

## Why contracts rather than coupling

The Sample Engine is C++20 because extraction is throughput-bound. The Tune and
Master engines are Python because graph algorithms and orchestration benefit
more from library maturity than from raw speed. Neither should be compromised
by the other's constraints.

That split costs an inter-process boundary. In exchange, each engine is
independently buildable, independently testable, and replaceable without
touching its neighbours — a new extraction backend for a different language
family only has to emit this format.

The boundary is only worth anything if it is specified. An undocumented format
is coupling with extra steps: both sides depend on the same layout, but nothing
records what that layout is, so a change on one side is discovered by the other
at runtime, in production, as corrupt data.

---

## AST Snapshot Format v1

**Producer:** Sample Engine (`sample_engine/src/snapshot.cpp`)
**Consumer:** Tune Engine (`tune_engine/raag_tune/snapshot_reader.py`)
**File extension:** `.raag.bin`
**Current version:** `2`

A snapshot holds the parsed abstract syntax trees of every source file in one
analysed repository.

### File layout

All multi-byte integers are **little-endian**, regardless of the host
architecture. All strings are **UTF-8**, length-prefixed, and **not**
null-terminated.

```
┌─────────────────────────────────────────────────────────┐
│ HEADER                                                  │
├──────────────────┬──────────┬───────────────────────────┤
│ magic            │ 4 bytes  │ ASCII "RAAG"              │
│ schema_version   │ uint32   │ Currently 2               │
│ file_count       │ uint32   │ Number of file records    │
└──────────────────┴──────────┴───────────────────────────┘

Repeated file_count times:

┌─────────────────────────────────────────────────────────┐
│ FILE RECORD                                             │
├──────────────────┬──────────┬───────────────────────────┤
│ path_length      │ uint32   │ Byte length of path       │
│ path             │ variable │ UTF-8, no terminator      │
│ node_count       │ uint32   │ Nodes in this file's AST  │
└──────────────────┴──────────┴───────────────────────────┘

  Repeated node_count times:

  ┌───────────────────────────────────────────────────────┐
  │ NODE RECORD                                           │
  ├─────────────────────┬──────────┬──────────────────────┤
  │ kind                │ uint8    │ See node kind values │
  │ name_length         │ uint32   │ Byte length of name  │
  │ name                │ variable │ UTF-8, may be empty  │
  │ byte_start          │ uint32   │ Source offset        │
  │ byte_end            │ uint32   │ Source offset, excl. │
  │ first_child_index   │ uint32   │ Index into this file │
  │ child_count         │ uint32   │ Contiguous children  │
  │ parent_index        │ int32    │ -1 for the root      │
  └─────────────────────┴──────────┴──────────────────────┘
```

Node records appear in arena order. **Index 0 is the root.** Indices are local
to the file record they appear in — they do not address across files.

### Field reference

| Field | Type | Meaning |
| ----- | ---- | ------- |
| `magic` | `char[4]` | Catches a wholly wrong file before any parsing happens. |
| `schema_version` | `uint32` | See [Schema versioning](#schema-versioning). |
| `file_count` | `uint32` | Number of file records that follow. May be `0`. |
| `path` | UTF-8 | Path as given to the Sample Engine. Not normalised or made absolute — the analysing tool's working directory is not the reader's problem. |
| `node_count` | `uint32` | May be `0` for a file that parsed to an empty tree. |
| `kind` | `uint8` | Normalised structural classification. |
| `name` | UTF-8 | Symbol name, or empty. **Empty is meaningful**: it records that the extractor could not resolve a name cleanly, not that the node is anonymous. The extractor declines to guess, because a wrong name becomes a confidently wrong dependency edge downstream. |
| `byte_start` | `uint32` | Offset of the node's first byte in the source file. |
| `byte_end` | `uint32` | Offset one past the node's last byte. Half-open, so `byte_end - byte_start` is the length. |
| `first_child_index` | `uint32` | Index of the first child. **Undefined when `child_count` is 0** — readers must check the count first. |
| `child_count` | `uint32` | Number of children stored contiguously from `first_child_index`. |
| `parent_index` | `int32` | Index of the parent, or `-1` for the root. Signed specifically to carry that sentinel. |

### Node kind values

| Value | Name | Meaning |
| ----- | ---- | ------- |
| `0` | `File` | Translation unit or module root. |
| `1` | `Class` | Class, struct, or union declaration. |
| `2` | `Function` | Function or method definition. |
| `3` | `Import` | Include directive, import statement, or using declaration. |
| `4` | `CallExpression` | Function or method invocation. |
| `5` | `Variable` | Variable, field, or parameter declaration. |
| `6` | `Other` | Anything not classified above. |
| `7` | `FieldAccess` | A read or write of a member field through the enclosing instance (`self.x` in Python, `this->x` in C++). Added in schema version 2. |

**These values are fixed by the format.** Reordering them, or inserting a
member anywhere but the end, silently reinterprets every existing snapshot —
every `Class` becomes a `Function`, and nothing errors. New kinds append at the
end and increment the schema version.

The classification is deliberately coarse. RAAG reasons about architecture, not
syntax, so it needs to know that something is a function without caring whether
the grammar spelled it `function_definition` or `function_declaration`.

### Structural invariants

A conforming writer guarantees, and a reader may assume:

1. **Index 0 is the root** of each file's arena, and is the only node with
   `parent_index == -1`.
2. **Children are contiguous.** For any node, its children occupy exactly the
   index range `[first_child_index, first_child_index + child_count)`. This is
   what makes an index range a valid way to refer to children at all, and it is
   the reason the Sample Engine's tree walk is breadth-first — a depth-first
   walk would interleave grandchildren between siblings and break it.
3. **Every child points back.** For every node at index `i` and every child
   index `c` in its range, `node[c].parent_index == i`.
4. **Child ranges stay in bounds.** `first_child_index + child_count <=
   node_count`.
5. **Byte ranges nest.** A child's `[byte_start, byte_end)` falls inside its
   parent's.

These are asserted directly in the Sample Engine's test suite rather than
inferred from parse output, because a violation surfaces downstream as
incorrect dependency edges — far harder to diagnose than a failed assertion.

---

## Schema versioning

### Version history

| Version | Change |
| ------- | ------ |
| 1 | Initial format. |
| 2 | Added `FieldAccess` (kind `7`), recording member accesses through the enclosing instance. Cohesion analysis needs to know which method touches which field, and that relation cannot be recovered from declarations alone. While readers treating unknown kinds as `Other` are technically structurally compatible with this addition, the strict version bump prevents older readers from silently misclassifying the data. |

`schema_version` is **independent of the platform version**. RAAG can go from
`0.4.0` to `0.5.0` without touching it, and a change to it can force a major
bump even in an otherwise minor release.

The reader **rejects an unrecognised version outright.** It does not attempt a
best-effort parse. A strict version bump triggers an immediate rejection rather than best-effort backward-compatible parsing, unless the reader has been explicitly updated to support a range of versions.

This is the important design decision in the whole format. Reading a changed
layout as though it were the current one usually *succeeds* — the byte counts
still work out, the values are still in range — and produces plausible-looking
nonsense that propagates through the dependency graph into the metrics and out
the other side as an architectural conclusion that is simply wrong. There is no
error to trace back to. Failing loudly at load time, with a message naming both
versions, costs one confusing minute instead of one confusing week.

### Defensive limits

Beyond version checking, the reader validates before it allocates:

- **String lengths** are capped at 1 MiB. A corrupt four-byte length field
  could otherwise request a multi-gigabyte allocation from a small file.
- **Kind bytes** are range-checked before conversion. In C++, casting an
  out-of-range integer to a scoped enum is undefined behaviour, not merely a
  wrong value.
- **Every read** checks that enough bytes remain, so a truncated file raises at
  the point of truncation rather than producing a partially populated arena
  whose child ranges point past its own end.

---

## Changing the format

Any change to the byte layout — a new field, a reordered field, a widened
integer, a new enum value in a position other than last — is a **breaking
change to the Sample-to-Tune contract**.

Required, in this order:

1. **Update this document first.** The specification is the source of truth;
   the implementations conform to it, not the reverse.
2. **Increment `kSchemaVersion`** in `sample_engine/include/raag/snapshot.hpp`.
3. **Increment `SUPPORTED_SCHEMA_VERSION`** in
   `tune_engine/raag_tune/snapshot_reader.py`.
4. **Update both test suites** — the C++ round-trip tests and the Python
   synthetic-snapshot tests. Both construct the format independently; if only
   one is updated, the mismatch is caught immediately, which is the point.
5. **Record it in `CHANGELOG.md`** under `Changed`, with a migration note.
   Snapshots are cached artefacts; users need to know theirs are now stale.

Adding a new node kind at the end of the enum is the one change that is
backward-compatible for readers that treat unknown kinds as `Other`. It still
increments the version, because a reader that does *not* do that will
misclassify rather than fail.
