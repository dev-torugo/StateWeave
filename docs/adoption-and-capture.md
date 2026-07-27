# Existing-project adoption and Capture Inbox

## User contract

StateWeave has two distinct creation paths:

- `stateweave init` creates a standalone store in an empty destination;
- `stateweave adopt` places a self-contained sidecar in an existing project.

Adoption never turns existing files into memory. It does not scan repository
contents, edit `.gitignore`, create a commit, or infer facts from source code.
The only planned persistent write is `.stateweave-project/`.

All normal CLI commands discover either an embedded `stateweave.toml` or
`.stateweave-project/stateweave.toml` when `--config` points at a directory.
Discovery fails closed if both exist or if the supplied root, sidecar, or
configuration is a symlink.

## Dry-run and response states

Run a read-only plan first:

```bash
stateweave adopt ./existing-project \
  --id existing-project \
  --name "Existing Project"
```

The versioned `AdoptionPlan` returns one of:

| Status | Meaning | Mutation |
|---|---|---|
| `safe` | the fixed sidecar path is available | none |
| `blocked` | identity, path, symlink, invalid config, or interrupted-adoption conflict | none |
| `already_adopted` | a matching embedded or sidecar configuration is valid | none |

The plan contains the exact planned path, number of preserved top-level
entries, a name/type snapshot digest, conflicts, and `plan_sha256`. It hashes
the inventory but does not persist or echo unrelated project filenames.

Apply only the exact reviewed plan:

```bash
stateweave adopt ./existing-project \
  --id existing-project \
  --name "Existing Project" \
  --apply \
  --expected-plan-sha256 <plan-sha256> \
  --adopted-at 2026-07-27T19:00:00Z \
  --confirm-adopt
```

StateWeave recomputes the plan, acquires a fixed adoption lock, checks the
snapshot again, builds a complete store in a same-directory staging path, and
renames it to `.stateweave-project`. A handled failure removes only the lock
and staging path that StateWeave created. An abrupt interruption leaves those
paths as evidence; the next plan returns `blocked` instead of deleting them.

The sidecar contains its own versioned configuration, canonical memory,
metadata, and an immutable adoption receipt. Existing project files remain
outside the StateWeave backup boundary and are not copied.

## Capture request

Adoption answers where StateWeave lives. Capture answers how an explicitly
observed change enters the governed lifecycle.

`capture-import` accepts a complete adapter-neutral `CaptureRequest`:

```bash
stateweave capture-import ./capture-request.json \
  --config ./existing-project
```

The request declares:

- source adapter, stable source ID, and locator;
- `before` and `after` cursor values;
- capture time and observer;
- one to 100 uniquely identified events;
- candidate classification, confidence, source, and provenance;
- a complete proposed fact, decision, or state record;
- create/update intent and optimistic expected SHA-256.

StateWeave does not contact the declared source. `adapter` is evidence supplied
by the host, not proof that a Git, filesystem, CI, or runtime integration ran.

## Ingestion invariants

For a new request, StateWeave:

1. validates the official Draft 2020-12 request contract;
2. applies bounded content inspection to all supplied metadata and records;
3. derives `CAP-<sha256>` from the exact request;
4. requires the source checkpoint to equal `cursor.before`;
5. creates one deterministic `MemoryCandidate` per event;
6. forces `review_required: true`;
7. records the envelope ID in candidate derivation provenance;
8. persists the immutable envelope and advances the source checkpoint.

Replaying the same request returns the same envelope and candidates. A new
request with a stale `before` cursor fails before any write. If a process stops
after candidate persistence but before the envelope or checkpoint, replay
finishes the same identities. Until replay, `audit-capture` reports the orphan
binding.

Captured candidates never write canonical memory. Operators still use
`candidate-preview` and `promote-candidate --confirm-human`.

## Audit and backup

Run all stores participating in the flow:

```bash
stateweave audit-adoption --config ./existing-project
stateweave audit --config ./existing-project
stateweave audit-continuity --config ./existing-project
stateweave audit-capture --config ./existing-project
```

`audit-adoption` verifies the optional receipt digest and project identity.
`audit-capture` checks closed-world paths, schemas, request/envelope/checkpoint
digests, linear cursor chains, chain-head checkpoints, candidate digests,
mandatory review, provenance bindings, content findings, missing envelopes,
and unexpected entries.

Capture envelopes, checkpoints, candidates, and the adoption receipt are
ordinary verified extension artifacts. Standard backup and restore preserve
them. The backup intentionally excludes the host project's unrelated files.

## Current boundary

This slice provides a safe host-ingestion boundary, not automatic observation.
There is no Git adapter, filesystem watcher, CI client, runtime listener,
credential integration, background daemon, or direct promotion. Supporting a
concrete external source remains a separately approved adapter change.
