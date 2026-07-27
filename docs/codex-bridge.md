# Codex host bridge

## Purpose and boundary

The Codex bridge closes the local continuity loop without moving execution or
authority into StateWeave:

```text
task + manifest + worker + policy + MemoryQuery
                         |
                         v
             codex-prepare
       validate + compile + persist
                         |
                         v
 immutable SES-<sha256> + exact ContextBundle
 dispatch.execution_authorized = false
                         |
               external host decision
                         |
                         v
      complete observed receipt + evaluation
                         |
                         v
             codex-observe
      reconcile policy/effects/hashes
                         |
                         v
 orchestration episode + immutable OBS-<sha256>
```

The bridge does not contact Codex, select a model, submit a prompt, read a
transcript, grant an effect, or infer missing execution evidence. Concrete
model identifiers are accepted only inside the host-reported execution
receipt.

## Preparation contract

The public Python entrypoint is
`stateweave.adapters.prepare_codex_session`; the CLI entrypoint is
`stateweave codex-prepare`.

Preparation requires:

- a valid `MemoryQuery`;
- a valid task and its exact input manifest;
- one eligible worker whose `runtime_adapter` is `codex`;
- a project-owned `PolicyPack`;
- the worker role;
- a unique list of requested effect slugs;
- an approval reference for each human-gated effect that the host claims was
  approved;
- an explicit RFC 3339 creation timestamp.

The content inspector runs before persistence. Obvious secret-shaped content
blocks preparation without echoing its value. Instruction-shaped content is
retained as a warning.

For every effect, the session records `policy_allowed`, `requires_human`,
`approval_ref`, and the policy decision reason. `ready_for_host` is true only
when all policy preconditions are satisfied. It is a readiness signal, not
permission: `dispatch.execution_authorized` is always false.

The session embeds the exact task, manifest, worker, and `ContextBundle`. Its
identity is derived from the canonical payload and stored as
`.stateweave/extensions/adapters/codex/sessions/SES-<sha256>.json`. Replaying
the same preparation converges on the same immutable artifact.

## Observation contract

The public Python entrypoint is
`stateweave.adapters.record_codex_observation`; the CLI entrypoint is
`stateweave codex-observe`.

The host supplies complete, schema-backed execution receipt and evaluation
documents. StateWeave verifies:

- session, task, worker, manifest, and context bindings;
- `runtime_observation.adapter == "codex"`;
- the evaluation references the supplied receipt;
- every requested effect has exactly one observation;
- a succeeded effect was policy-allowed in the immutable session;
- approval references are unchanged;
- output names are declared by the task;
- a successful receipt contains every expected output;
- receipt/evaluation content passes the configured content boundary.

Only after validation does the bridge append task, manifest, worker, receipt,
and evaluation to the persistent orchestration ledger. It then writes an
immutable observation binding under
`.stateweave/extensions/adapters/codex/observations/OBS-<sha256>.json`.
Replay is idempotent. Reusing one receipt ID for a different observation fails
closed.

An approval reference is an evidence locator asserted by the host. StateWeave
does not independently prove that the named human approved the effect.

## Audit and recovery

Run all stores that participate in the flow:

```bash
stateweave audit --config ./my-memory
stateweave audit-continuity --config ./my-memory
stateweave audit-codex --config ./my-memory
```

`audit-codex` checks:

- closed-world session and observation directories;
- official Draft 2020-12 contracts;
- artifact filenames and canonical hashes;
- `execution_authorized: false`;
- embedded and separately stored context equality;
- task/manifest/worker eligibility;
- policy-decision and effect accounting;
- receipt/evaluation existence and digest equality in the continuity ledger.

A crash after ledger append but before observation write is recoverable by
replaying the same `codex-observe` request. The immutable document IDs make a
different replay fail rather than overwrite evidence.

The standard verified backup includes both adapter and continuity artifacts.
After restore, rerun all three audits.

## Current limitations

- There is no process launcher, Codex API client, credential integration, or
  transcript collector.
- The bridge validates approval references but does not verify an external
  approval system.
- Host truthfulness and runtime attestation remain outside StateWeave.
- There is no automatic filesystem, Git, CI, or runtime watcher.
- The implemented integration is proven with synthetic hosts and contracts,
  not with an operational Codex execution.
