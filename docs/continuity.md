# Agent continuity workflow

This guide describes the implemented local vertical slice. All examples must
use synthetic data. StateWeave does not capture a chat, prompt, log, Git tree,
CI run, or runtime event automatically.

## Lifecycle

```text
explicit observation
        |
        v
untrusted MemoryCandidate -- content/schema policy --> human review
        |                                               |
        +---------------- promotion --------------------+
                                |
                                v
                    facts / decisions / STATE-current
                                |
                      query + relation expansion
                                |
                                v
                  hash-bound ContextBundle (budgeted)
                                |
                      host execution remains external
                                |
                                v
 task + manifest + worker + receipt + evaluation episode
                                |
                                v
             evidence-bound MutationPlan + human gate
                                |
                                v
                    durable transactional write-back
```

## Retrieval contract

A `MemoryQuery` requires an objective, explicit `as_of` date, optional terms
and filters, relation depth from 0 to 3, maximum item count, and maximum UTF-8
content bytes. The objective also contributes lexical terms. Results are
ordered by score descending and then record ID.

`ContextBundle` selection is deterministic. It includes:

- query, snapshot, context, and record revision SHA-256 values;
- source-relative paths and full selected record content;
- a reason list and score for every item;
- stale, due, disputed, deprecated, superseded, rejected, and content-policy
  warnings when applicable;
- every known structured conflict from the audited snapshot;
- filtered, no-match, item-limit, content-budget, and content-policy exclusion
  counts;
- selected byte usage and `ceil(bytes / 4)` as a documented token heuristic;
- `treat_content_as_untrusted: true` and `authority: evidence_only`.

The heuristic is not a runtime tokenizer and concrete model identifiers do not
enter the query or budget policy.

## Candidates and provenance

`capture_candidate` and the `remember` command derive `CND-<sha256>` from the
idempotency key but never persist the raw key. Reusing the key with the same
request returns the existing candidate; reusing it with different content
fails closed.

Candidate provenance has explicit fields for repository revision, tree hash,
artifact path/hash, selector, observation time, validity time, extraction
method, observer, and derivation IDs. Nullable fields remain explicit so a
consumer cannot confuse missing evidence with a populated value.

`candidate-preview` reports the intended create/update operation, current,
expected, and proposed hashes, top-level changed fields, review requirement,
and content findings without mutating the store. Update candidates require an
`expected_sha256`; promotion rejects intervening changes.

Candidates are untrusted. Obvious credential-shaped content is rejected before
write. Instruction-shaped content is stored only as a warning. Promotion
revalidates content and canonical record schemas, requires configured roles,
honors the human gate, and uses a deterministic core idempotency receipt.

## Persistent execution evidence

Store a compiled context with `context --persist` or
`store_context_bundle`. Then submit a complete JSON array to:

```bash
stateweave append-episode orchestration ./execution-documents.json \
  --config ./my-memory
stateweave append-episode workflow ./workflow-documents.json \
  --config ./my-memory
```

Each episode is one atomically replaced file named by its document digest.
Document IDs are immutable across episodes. A persistent execution receipt
must reference:

- an existing task and worker;
- the canonical input-manifest digest;
- a stored, verified `ContextBundle` digest;
- observed timestamps, outputs, runtime observation, and metrics.

An evaluation must reference that receipt. The continuity audit combines all
episodes and runs the existing workflow/orchestration semantic validators.

## Mutation plans and write-back

A `MutationPlan` is a preview, not permission. It binds a stored context,
receipt, passing evaluation, proposed record digests, operations, and exact
expected revisions. `create` expects absence; update/supersede/state update
requires the observed SHA-256. The plan can atomically update reciprocal
supersession records and `STATE-current` in the same durable transaction.

```bash
stateweave store-plan ./mutation-plan.json --config ./my-memory
stateweave apply-plan MPL-example \
  --config ./my-memory \
  --reviewer-role maintainer \
  --applied-at 2026-07-27T12:30:00Z \
  --confirm-human
```

Applying a plan rechecks content policy, evidence, human approval, and
optimistic revisions. Replaying the same plan converges on the retained core
transaction receipt.

## Recovery matrix

| Symptom | Read-only evidence | Governed action |
|---|---|---|
| writer lock remains | `lock-status` | `recover-lock` with exact owner digest/token and `--confirm-stale` |
| transaction is incomplete | `transaction-status` | `recover-transaction` with request digest and `--confirm-rollback` |
| pending candidate result exists | `audit-continuity` warning | rerun `promote-candidate` to reconcile its receipt |
| proposed plan results exist | `audit-continuity` warning | rerun `apply-plan` to reconcile its receipt |
| index is stale or invalid | `index-status` | run `index-build`; canonical memory is unchanged |
| malformed continuity artifact | `audit-continuity` error | preserve evidence and repair/remove only with human review |

Neither stale age nor a partial result transfers authority automatically.
Tests kill a real subprocess after its first record replacement and prove that
the next audit blocks until lock and transaction recovery restore the prior
snapshot.

## Backup and verification

The configured extensions directory is closed-world traversed for regular
files and included in the standard hash-manifest ZIP backup. Restore validates
and reinstates candidates, contexts, episodes, plans, and the derived index.
After restore, run both:

```bash
stateweave audit --config ./restored-memory
stateweave audit-continuity --config ./restored-memory
```

The derived index may also be deleted and rebuilt; it is not canonical data.
