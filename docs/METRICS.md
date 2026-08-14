# Metrics Reference

Every number RAAG reports, how it is computed, and what it means. Each metric
comes from published software engineering literature; where RAAG deviates or
approximates, that is stated rather than glossed.

---

## Table of Contents

- [Why quantify architecture](#why-quantify-architecture)
- [Coupling metrics](#coupling-metrics)
  - [Afferent Coupling (Ca)](#afferent-coupling-ca)
  - [Efferent Coupling (Ce)](#efferent-coupling-ce)
  - [Instability (I)](#instability-i)
- [Cohesion metrics](#cohesion-metrics)
  - [LCOM1](#lcom1)
  - [LCOM4](#lcom4)
- [Structural metrics](#structural-metrics)
- [Thresholds and policy](#thresholds-and-policy)
- [Known limitations](#known-limitations)
- [References](#references)

---

## Why quantify architecture

"This module is too coupled" is an opinion. "Eleven files depend on this one and
it depends on nine others" is a fact, and two engineers who disagree about the
first can at least agree about the second.

Quantification does not replace judgement — a high instability figure is
sometimes exactly right — but it moves the argument from taste to evidence, and
it makes drift visible over time in a way that reading code never does.

---

## Coupling metrics

Coupling metrics are computed from the dependency graph: nodes are files, and an
edge runs **from the dependent to the dependency**. If `a.cpp` includes
`b.hpp`, the edge is `a → b`.

That direction is the entire basis of what follows. Reverse it and every figure
below inverts while remaining superficially plausible.

### Afferent Coupling (Ca)

> **Ca(m) = |{ x : x → m }|** — the in-degree of *m*.

How many files depend on this one.

High Ca means the module carries responsibility. Many things rest on it, so a
change ripples outward and the cost of getting it wrong is proportional to Ca.
Foundational utilities, core types, and shared interfaces all have high Ca, and
that is correct — it is what being foundational means.

A high-Ca module is not a problem. A high-Ca module that *changes often* is.

### Efferent Coupling (Ce)

> **Ce(m) = |{ x : m → x }|** — the out-degree of *m*.

How many files this one depends on.

High Ce means fragility. Every dependency is a reason this file might have to
change through no fault of its own. A file depending on thirty others is
downstream of thirty separate change streams.

High Ce is usually a smell, but not always: an application entry point or a
composition root legitimately wires many things together. What matters is
whether the dependencies are *incidental* or *intentional*.

### Instability (I)

> **I = Ce / (Ca + Ce)**, ranging from 0.0 to 1.0.

| Value | Meaning |
| ----- | ------- |
| **0.0** | Maximally stable. Depended upon, depends on nothing. Hard to change, but nothing forces it to. |
| **1.0** | Maximally unstable. Depends outward, nothing depends on it. Cheap to change, at the mercy of everything upstream. |

**A high instability figure is not by itself a defect.** An adapter, a plugin, a
CLI entry point — these *should* be unstable. Depending outward and being
depended upon by nothing is precisely their job.

What RAAG flags is a module that is **both depended upon and unstable**: something
sits beneath other code while itself resting on shifting ground. That is why the
instability check is gated on a minimum afferent coupling rather than firing on
the ratio alone.

**Special case.** A module with `Ca = Ce = 0` has no meaningful instability. RAAG
returns `0.0` by convention, but such a module is reported as *isolated* rather
than *stable* — those are different facts and conflating them would be
misleading.

---

## Cohesion metrics

Coupling describes relationships *between* files. Cohesion asks whether a single
class holds together: are its methods working on the same state, or on unrelated
concerns that happen to share a declaration?

Both variants below need to know **which method touches which field**. That
relation is captured by the `FieldAccess` node kind, introduced in snapshot
schema version 2 specifically to make cohesion computable — it cannot be
recovered from class, method, and field declarations alone.

Only accesses through the enclosing instance count: `self.x` in Python,
`this->x` in C++. An access through some other object is a dependency on that
object, not a use of this class's own state, and counting it would make every
class appear cohesive regardless of how its methods actually relate.

### LCOM1

> Let **P** be the number of method pairs sharing no field, and **Q** the number
> sharing at least one.
> **LCOM1 = max(0, P − Q)**

The original Chidamber & Kemerer definition. Zero is cohesive; higher is worse.

Floored at zero because the raw formula goes negative when most pairs share
state, and a negative "lack of cohesion" is not meaningful — it means very
cohesive, which zero already expresses.

**LCOM1 overstates on classes with accessors.** A getter touching one field
appears unrelated to every method that does not touch that field, so a
well-designed class with several properties can score badly. RAAG reports LCOM1
but does not gate on it.

### LCOM4

> Build an undirected graph where methods are nodes. Join two methods when they
> **share a field** or when **one calls the other**.
> **LCOM4 = the number of connected components.**

The Hitz & Montazeri definition, and RAAG's primary cohesion figure.

| Value | Meaning |
| ----- | ------- |
| **0** | No methods. |
| **1** | Cohesive — every method relates to the others, directly or transitively. |
| **n > 1** | The class splits cleanly into *n* independent groups. |

LCOM4 is preferred over LCOM1 for two reasons.

**It is actionable.** A score of 3 does not just say "incohesive" — the three
components *name the classes this should have been*. RAAG reports the groupings
alongside the number.

**Call edges fix the accessor problem.** A method that delegates entirely to
another touches no fields itself. Under LCOM1 it looks isolated; under LCOM4 the
call edge connects them, which is the truthful answer.

When LCOM1 is high but LCOM4 is 1, the usual explanation is accessors rather
than a design problem. The two disagreeing is itself informative.

**Classes with fewer than two methods are trivially cohesive** — there is no
pair that could fail to relate. RAAG does not judge them, because reporting them
would bury the real findings in noise.

---

## Structural metrics

| Metric | Definition | Reading |
| ------ | ---------- | ------- |
| **Dependency cycles** | Strongly connected components of size > 1 | Files that mutually depend. None can be understood, tested, or changed independently of the rest. |
| **Dependency layers** | Topological generations of the condensed graph | Layer 0 depends on nothing internal; layer *n* depends only on layers below. The shape an architecture diagram wants. |
| **Density** | `E / (V × (V−1))` | Near 0 is sparse and layered. A figure rising over time means modules are becoming more interconnected. |
| **Isolated files** | `Ca = Ce = 0` | Neither depended upon nor depending. Often dead code, sometimes an entry point. |

Cycles are found via strongly connected components rather than by enumerating
simple cycles. Enumeration is exponential in the worst case, and a densely
tangled header set is exactly the worst case — the analysis would hang on the
input it is most needed for.

---

## Thresholds and policy

Metrics are numbers; thresholds are policy. They live in configuration rather
than in the metric functions, because a tool that hard-codes them will be wrong
about half the repositories it sees.

| Threshold | Default | Rationale |
| --------- | ------- | --------- |
| `max_instability` | `0.4` | Applied only to modules meeting the afferent gate below. |
| `min_afferent_for_instability` | `3` | Below this, instability is not judged — an unstable leaf is uninteresting because nothing breaks when it changes. |
| `max_efferent_coupling` | `20` | Warning only. Composition roots legitimately exceed it. |
| `max_lcom4` | `1` | Anything above 1 means the class splits. |
| `min_methods_for_cohesion` | `3` | Below this, cohesion is not meaningfully measurable. |
| `fail_on_cycles` | `true` | Cycles are structural defects rather than matters of degree. |

### Errors versus warnings

Only **error**-severity violations fail a run. Warnings surface signals worth
looking at without blocking anything.

The reasoning is practical: a gate that blocks on every soft signal gets
disabled, and a disabled gate protects nothing.

---

## Known limitations

Stated plainly, because a metric whose limits are undocumented invites more
confidence than it earns.

**Instability lacks its other half.** Martin pairs instability with
*abstractness* (**A**) and measures distance from the main sequence,
**D = |A + I − 1|**. That model correctly distinguishes an abstract, unstable
interface layer from a concrete, unstable tangle. RAAG's afferent gate is a
cruder approximation of the same intent. Implementing A and D is the single
highest-value analytical addition on the roadmap.

**Coupling is only as good as import resolution.** Edges come from imports
matched against the parsed file set, not from a compiler include path or
`sys.path`. Every run reports its resolved / ambiguous / external ratio; read it
before trusting the coupling figures.

**Call edges are absent from the dependency graph.** Resolving a callee to a
definition requires overload sets, virtual dispatch targets, and namespace
lookup, none of which survive in a parse tree. Method-to-method calls *are* used
within LCOM4, where the scope is one class and name matching is reliable.

**C++ field access detection is narrower than Python's.** `this->x` is detected;
a bare `x` referring to a member is not, because distinguishing it from a local
variable requires scope resolution. C++ classes that access members without
`this->` will show artificially low field sharing, and therefore inflated LCOM.
Codebases using a member-naming convention such as `x_` are affected least.

**Metrics are computed per snapshot, not over history.** A module's instability
today says less than its instability trend across the last hundred commits.
Historical analysis is on the roadmap.

---

## References

- Martin, R. C. *Agile Software Development: Principles, Patterns, and Practices*. Prentice Hall, 2002. — Ca, Ce, I, A, D.
- Chidamber, S. R. and Kemerer, C. F. "A Metrics Suite for Object Oriented Design." *IEEE Transactions on Software Engineering*, 20(6), 1994. — LCOM1.
- Hitz, M. and Montazeri, B. "Chidamber and Kemerer's Metrics Suite: A Measurement Theory Perspective." *IEEE Transactions on Software Engineering*, 22(4), 1996. — LCOM4.