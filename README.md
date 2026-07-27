# StateWeave

StateWeave is a provisional public development framework for persistent
project memory, governed workflows, auditable orchestration, and runtime
adapters.

The repository is publicly viewable, but the project is under active
development and is not a release. Its intended licensing direction is
source-available for non-commercial use, with commercial use available under a
separate agreement. It must not be described as open source under the Open
Source Initiative definition. No definitive license has been applied, so the
repository does not currently grant usage or redistribution rights.

## Current development checkpoint

The current codebase implements a runtime-neutral continuity slice and an
explicit Codex host bridge on top of the governed `memory-core`:

- versioned project configuration;
- facts, decisions, and current state;
- configurable TTL and review queues;
- reciprocal supersession and backlinks;
- structured conflict detection;
- crash-recoverable record transactions, optimistic revisions, idempotency
  keys, and governed stale-lock recovery;
- explicit migration, backup, and restore;
- official JSON Schema Draft 2020-12 validation;
- explainable query and deterministic `ContextBundle` compilation under a
  UTF-8 byte budget;
- a hash-bound, rebuildable retrieval index with safe scan fallback;
- untrusted candidates, human-gated promotion, persistent workflow and
  orchestration episodes, receipts, evaluations, and governed write-back;
- bounded content-policy hooks for ingress and retrieval;
- immutable, context-bound Codex session preparation and host-reported
  receipt/evaluation reconciliation;
- synthetic multi-process, abrupt-exit, backup/restore, and performance tests;
- positive, negative, and adversarial tests.

Official schema validation is mandatory for configuration loading, audits,
record writes, and post-migration verification. Cross-record invariants remain
separate semantic checks.

See `docs/project-plan.md` for the full extraction sequence and gates.

## Optional modules

- deterministic retrieval, `ContextBundle`, and derived indexing;
- persistent candidates, episodes, receipts, evaluations, and mutation plans;
- bounded content inspection with project-replaceable policy hooks;
- governed workflow records and lifecycle audit;
- orchestration DAGs, deterministic routing, manifests, receipts, and
  evaluations;
- runtime-neutral dispatch envelopes and explicit adapter registry;
- passive Codex envelope adapter plus a persistent, policy-aware host bridge;
- opt-in allow-listed telemetry and read-only observation;
- project-owned policy packs with non-bypassable human gates.

See `docs/continuity.md` for the end-to-end lifecycle,
`docs/codex-bridge.md` for the host integration, `docs/performance.md` for
measured local evidence, and `docs/extensions.md` for extension and authority
contracts.

The local extraction evidence is recorded in
`docs/verification-report-2026-07-25.md`; the public-repository authorization
and remaining release gates are recorded in
`docs/publication-report-2026-07-26.md`. The continuity implementation landed
through PR #1 with its hosted matrix green; its historical evidence is in
`docs/verification-report-2026-07-27.md`. Host-bridge evidence and limitations
are recorded separately in
`docs/verification-report-host-bridge-2026-07-27.md`.
