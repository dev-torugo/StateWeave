# Architecture

## Dependency direction

```text
policy packs ─┐
adapters ─────┼──> optional runtime/orchestration/workflow modules
              │
applications ─┴──> memory-core
                         │
                         ├── configuration
                         ├── Draft 2020-12 schemas
                         ├── semantic graph audit
                         ├── atomic store and writer lock
                         └── migrations and backups
```

`memory-core` has no knowledge of a source project, fixed human authority,
concrete model, repository layout, or runtime surface. Optional modules may
depend on core; core never imports them.

## Optional modules

- `stateweave.workflow`: schema-backed work requests, handoffs, acceptances,
  and lifecycle audit;
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
Record updates use a temporary sibling followed by `os.replace`. A migration
creates a ZIP backup and a hash journal before replacing records. Restore
extracts only safe relative members into a new or explicitly empty
destination.
