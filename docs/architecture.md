# Architecture

## Dependency direction

```text
applications ─┬─> continuity ─┬─> context retrieval/index ─┐
              │               ├─> workflow/orchestration ──┤
              │               └─> content policy hooks ────┤
              ├─> runtime adapters ────────────────────────┤
policy packs ─┴────────────────────────────────────────────┤
                                                          v
                                                    memory-core
                                                          │
                                                          ├─ configuration
                                                          ├─ Draft 2020-12
                                                          ├─ graph audit
                                                          ├─ transactions/lock
                                                          └─ backup/migration
```

`memory-core` has no knowledge of a source project, fixed human authority,
concrete model, repository layout, or runtime surface. Optional modules may
depend on core; core never imports them.

## Optional modules

- `stateweave.workflow`: schema-backed work requests, handoffs, acceptances,
  and lifecycle audit;
- `stateweave.context`: explainable lexical retrieval, graph expansion,
  deterministic `ContextBundle` compilation, and a rebuildable index;
- `stateweave.continuity`: untrusted candidates, immutable contexts, atomic
  episodic batches, persistent receipts/evaluations, and mutation plans;
- `stateweave.content`: bounded, effect-free content inspection protocol and a
  conservative baseline;
- `stateweave.orchestration`: task DAGs, routing, input manifests, execution
  receipts, and evaluations;
- `stateweave.runtime`: dispatch envelopes, adapter protocol, and explicit
  registry;
- `stateweave.adapters`: runtime-specific translations, including the passive
  Codex adapter;
- `stateweave.telemetry`: opt-in allow-listed buffer and read-only observer;
- `stateweave.policy`: project-owned roles, authority effects, human gates,
  routing ceilings, and telemetry settings.

None of these packages performs an external effect merely by loading,
validating, routing, or preparing a document.

## Memory graph

Facts, decisions, and current-state records form a directed graph:

- `references` produces deterministic backlinks;
- `supersedes` and `superseded_by` are reciprocal edges;
- a supersession edge may connect records only within the same kind;
- supersession cycles are invalid;
- two active verified facts with the same structured claim key and different
  values conflict unless one transitively supersedes the other.

JSON Schema validates each record in isolation. Semantic validation resolves
cross-record identifiers, graph invariants, configured TTLs, and conflicts.

## Writes and recovery

Writers acquire an atomic directory lock scoped to the configured store.
Record updates first persist a hash-bound recovery journal and before/after
payloads, then move through `preparing`, `prepared`, `applying`, and a terminal
state. Every file replacement uses a temporary sibling, `fsync`, and
`os.replace`. Idempotency receipts survive successful writes; an interrupted
batch blocks audit until exact-fingerprint rollback. Optimistic SHA-256
preconditions prevent lost updates.

A migration creates a ZIP backup and hash journal before replacing records.
Restore extracts only safe relative members into a new or explicitly empty
destination. Extension artifacts are opaque to core semantics but are
closed-world discovered and included in the verified backup.

## Retrieval and continuity

Context retrieval takes an explicit `as_of`, filters, relation depth, and
content budget. Ranking is lexical and deterministic. Each selected item
contains its record ID, source-relative path, revision SHA-256, score, and
selection reasons. The bundle exposes review warnings, governed statuses,
known structured conflicts, exclusions, byte usage, and a heuristic token
estimate. Its digest excludes no selected evidence and is independent of wall
clock time.

The optional index stores a previously audited snapshot. It is used only when
the configuration hash, audit date, closed layout, transaction state, every
source path, and every record hash still match. Otherwise retrieval performs
the canonical audit/load path. The index is a cache, never a source of truth.

Continuity artifacts live below the configured generic extension path. A
receipt is persistable only when it binds both its input-manifest SHA-256 and a
stored `ContextBundle` SHA-256. A mutation plan additionally binds a passing
evaluation and uses the same core transaction path for fact, decision, and
`STATE-current` write-back. Loading or validating an adapter never grants
authority for an external effect.
