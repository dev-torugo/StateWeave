# Quickstart

StateWeave currently targets Python 3.11 or newer. The repository is a local
development extraction and has no published package or definitive license.

## Install from a local checkout

Create a clean virtual environment, then install the checkout:

```bash
python -m venv .venv
.venv/bin/python -m pip install /path/to/StateWeave
```

On Windows, use `.venv\Scripts\python` in place of `.venv/bin/python`.

## Initialize a consumer

```bash
stateweave init ./my-memory \
  --id my-memory \
  --name "My Synthetic Memory"
```

Initialization refuses a non-empty destination. It creates:

```text
my-memory/
├── stateweave.toml
├── memory/
│   ├── facts/
│   ├── decisions/
│   └── state/current.json
└── .stateweave/
    ├── backups/
    └── migrations/
```

## Audit and review

```bash
stateweave audit --config ./my-memory
stateweave audit --config ./my-memory --json
stateweave review --config ./my-memory
stateweave backlinks FCT-example --config ./my-memory
```

The CLI validates every record against its packaged official Draft 2020-12
schema before evaluating cross-record semantic invariants. An audit succeeds
only when both layers pass.
Stale verified facts, broken references, nonreciprocal supersession, cycles,
unconfigured roles, TTL violations, and structured claim conflicts fail the
audit according to project policy.

## Backup and restore

```bash
stateweave backup --config ./my-memory --label before-change
stateweave restore /path/to/backup.zip ./restored-memory
```

Restore requires an empty destination, verifies every member hash, rejects
duplicates, symlinks, unexpected members, and path traversal, and never calls
ZIP `extractall`.

## Migrate legacy facts

Preview first:

```bash
stateweave migrate \
  --config ./my-memory \
  --from-version 0.1 \
  --to-version 1.0
```

Apply only after reviewing the plan:

```bash
stateweave migrate \
  --config ./my-memory \
  --from-version 0.1 \
  --to-version 1.0 \
  --apply
```

Apply creates a pre-migration backup and hash journal. A failed write or
post-migration audit triggers rollback and leaves the journal observable.
