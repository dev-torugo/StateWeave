# Adoption and Capture Inbox verification — 2026-07-27

## Scope and evidence boundary

This report covers the repository-native adoption and adapter-neutral capture
slice based on merge `4f347b13d007bf667ca94a22b3d1215f5c23f81f`.
All existing-project fixtures, capture events, proposed records, locators, and
timestamps are synthetic.

The report does not claim that StateWeave inspected an operational repository,
ran Git, watched a filesystem, contacted CI, observed a runtime, launched
Codex, or independently verified host truth. It makes no release, package,
license, trademark, support, or maturity claim.

## Proven behavior

- Read-only adoption returns `safe`, `blocked`, or `already_adopted`.
- The plan binds project identity, preserved-entry count, inventory digest,
  conflicts, exact planned writes, and a canonical SHA-256.
- Apply requires the reviewed hash, timestamp, and explicit confirmation.
- Only `.stateweave-project/` is added to a synthetic existing project.
- Existing project bytes remain unchanged.
- Config discovery finds the sidecar and rejects ambiguity or symlinks.
- A versioned request produces an immutable envelope, linear checkpoint, and
  review-required candidates.
- Request replay is idempotent; stale cursor forks fail before persistence.
- Interrupted ingestion is detectable and recoverable by replay.
- Secret-shaped input blocks without value echo; instruction-shaped input is
  retained as an explicit warning.
- Backup and restore preserve capture envelopes, checkpoints, candidates, and
  extension evidence.
- A second independently loaded session retrieves a promoted captured fact
  without knowing its identifier.

## Local verification

The canonical local gate passed after implementation:

- extraction, neutrality, schema, provenance, and licensing-hold contracts:
  OK;
- 120 unit, integration, negative, concurrency, and adversarial tests: OK;
- migration/backup/restore release drill: OK.

Additional checks passed:

- Ruff 0.12.12: 64 Python files formatted and no lint findings;
- mypy 1.20.2: no findings in 39 source files;
- every new optional schema valid under Draft 2020-12;
- isolated wheel build, schema inclusion, clean install, public import, and CLI
  smoke: OK;
- local wheel SHA-256:
  `c5268be3aa293fa0ca50a7b72c4bcbf1a04d80065f1e32c20824d6aa29a98bb3`.

Hosted CI belongs to the pull-request commit carrying this report and is not
pre-claimed here.

## Remaining gates

- No concrete source adapter or automatic watcher exists.
- The host constructs proposed canonical records; StateWeave validates but
  does not infer them from arbitrary project content.
- Capture checkpoints serialize one logical source but do not provide a
  distributed lease.
- Sidecar VCS, classification, access, retention, and deletion policy belong
  to the adopting project.
- Legal, naming, release, and package-publication decisions remain external.
