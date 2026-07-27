# Quickstart

StateWeave currently targets Python 3.11 or newer. This is a public development
repository, but there is no published package, release, definitive license, or
current support claim. The commands below operate on a local checkout.

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
    ├── extensions/
    ├── migrations/
    └── transactions/
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

Optional continuity artifacts have a separate audit:

```bash
stateweave audit-continuity --config ./my-memory
stateweave audit-codex --config ./my-memory
```

Run the memory audit plus every audit for the optional stores in use before
backup or handoff.

## Query and compile context

The CLI defaults to verified facts and accepted decisions. Use an explicit
`as_of` for reproducible output:

```bash
stateweave query "durable storage decision" \
  --config ./my-memory \
  --as-of 2026-07-27 \
  --term durable \
  --term storage

stateweave context "durable storage decision" \
  --config ./my-memory \
  --as-of 2026-07-27 \
  --term durable \
  --term storage \
  --max-items 8 \
  --max-content-bytes 12000 \
  --persist
```

`--persist` stores the verified bundle for a later execution receipt. Build or
inspect the optional derived index with:

```bash
stateweave index-build --config ./my-memory --as-of 2026-07-27
stateweave index-status --config ./my-memory --as-of 2026-07-27
```

## Capture and promote a candidate

`remember` reads a proposed fact, decision, or state JSON file. It stores an
untrusted candidate; it does not promote the record:

```bash
stateweave remember ./proposed-fact.json \
  --config ./my-memory \
  --idempotency-key session-a-observation-1 \
  --captured-at 2026-07-27T12:00:00Z \
  --classification internal \
  --confidence high \
  --source-type filesystem \
  --source-locator synthetic/source.py \
  --observed-at 2026-07-27T12:00:00Z \
  --artifact-path synthetic/source.py \
  --artifact-sha256 <sha256> \
  --as-of 2026-07-27T12:00:00Z \
  --extraction-method syntax-parser \
  --observer local-agent
```

Preview hashes and changed top-level fields without mutating memory:

```bash
stateweave candidate-preview CND-<digest> --config ./my-memory
```

For an update candidate, pass `--operation update --expected-sha256 <sha256>`
to `remember`. The promotion will reject any intervening record revision.
After reviewing the returned `CND-...` candidate and preview:

```bash
stateweave promote-candidate CND-<digest> \
  --config ./my-memory \
  --reviewer-role maintainer \
  --promoted-at 2026-07-27T12:05:00Z \
  --confirm-human
```

See `docs/continuity.md` for receipt/evaluation episodes, mutation plans, and
recovery commands.

## Prepare and reconcile a Codex host session

Preparation reads complete schema-backed task, manifest, worker, query, and
policy files. It persists the exact context and returns an immutable session;
it does not launch Codex:

```bash
stateweave codex-prepare \
  ./task.json \
  ./input-manifest.json \
  ./worker.json \
  ./memory-query.json \
  --config ./my-memory \
  --policy ./policy-pack.json \
  --role contributor \
  --created-at 2026-07-27T18:00:00Z \
  --requested-effect write-files \
  --approval write-files=APR-reviewed-change
```

Review `ready_for_host`, every authority decision, content finding, and the
embedded context. `dispatch.execution_authorized` is always `false`; the host
owns the separate execution decision.

After execution, the host must provide complete receipt and evaluation JSON
documents. The receipt must bind the returned session, context, manifest,
worker, outputs, and every requested effect:

```bash
stateweave codex-observe SES-<digest> \
  ./execution-receipt.json \
  ./evaluation.json \
  --config ./my-memory \
  --observer synthetic-host \
  --observed-at 2026-07-27T18:05:00Z

stateweave audit-codex --config ./my-memory
stateweave audit-continuity --config ./my-memory
```

See `docs/codex-bridge.md` for the exact authority and persistence contract.

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
