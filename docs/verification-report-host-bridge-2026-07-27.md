# Codex host bridge verification — 2026-07-27

## Scope and evidence boundary

This report covers the local branch based on
`c3cf585f60b077edb610216f6f7eb77bb2e91cfe`. It proves contract composition and
persistence with synthetic host documents. It does not claim that Codex was
launched, that an external effect occurred, that a human approval reference
was independently verified, or that a runtime was attested.

It also makes no release, package-publication, licensing, trademark, maturity,
or operating-system support claim. Hosted evidence belongs to the pull request
and commit that carry this change; it is not pre-claimed here.

## Implemented evidence

- `codex-prepare` validates task, manifest, eligible worker, policy, role,
  requested effects, approval references, query, and content.
- Preparation persists the exact `ContextBundle` and an immutable,
  hash-derived session.
- Every embedded dispatch has `execution_authorized: false`.
- `codex-observe` accepts complete host-reported receipt/evaluation documents;
  it does not synthesize missing evidence.
- Receipts bind session, task, worker, input manifest, context, outputs,
  runtime observation, metrics, and every requested effect.
- Denied effects cannot be reconciled as succeeded.
- Observation replay is idempotent; identity collisions fail closed.
- `audit-codex` validates the adapter store and its continuity-ledger bindings.
- Adapter and continuity artifacts remain inside the verified extension
  backup boundary.

## Local verification

The canonical gate passed after implementation:

- extraction contracts: OK;
- 100 unit, integration, negative, concurrency, and adversarial tests: OK;
- migration/backup/restore release drill: OK.

Additional checks passed:

- Ruff 0.12.12: 58 Python files formatted and no lint findings;
- mypy 1.20.2: no findings in 35 source files;
- all packaged optional schemas valid under Draft 2020-12;
- isolated sdist/wheel build, adapter schema inclusion, clean wheel import, and
  packaged CLI smoke: OK.

Focused tests cover:

- preparation, observation, persistent ledger, replay, and audit;
- verified backup/restore of session, observation, context, and ledger;
- denied and human-gated effects;
- instruction-shaped warnings and secret blocking without value echo;
- authority-field tampering and unexpected adapter entries;
- the three public CLI entrypoints.

## Remaining gates

- The adapter does not launch or authenticate to Codex.
- Approval-system verification and runtime attestation remain host concerns.
- An operational integration requires its own external-system authorization.
- Automatic watchers and incremental capture plugins remain future work.
- Legal licensing, contribution, naming, release, and publication gates remain
  external and human-controlled.
