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

## Current checkpoint

The first implementation checkpoint is a complete `memory-core` vertical
slice:

- versioned project configuration;
- facts, decisions, and current state;
- configurable TTL and review queues;
- reciprocal supersession and backlinks;
- structured conflict detection;
- atomic writes and a cross-platform writer lock;
- explicit migration, backup, and restore;
- official JSON Schema Draft 2020-12 validation;
- positive, negative, and adversarial tests.

Official schema validation is mandatory for configuration loading, audits,
record writes, and post-migration verification. Cross-record invariants remain
separate semantic checks.

See `docs/project-plan.md` for the full extraction sequence and gates.

## Optional modules

- governed workflow records and lifecycle audit;
- orchestration DAGs, deterministic routing, manifests, receipts, and
  evaluations;
- runtime-neutral dispatch envelopes and explicit adapter registry;
- passive Codex adapter;
- opt-in allow-listed telemetry and read-only observation;
- project-owned policy packs with non-bypassable human gates.

See `docs/extensions.md` for the extension and authority contracts.

The local extraction evidence is recorded in
`docs/verification-report-2026-07-25.md`; the public-repository authorization
and remaining release gates are recorded in
`docs/publication-report-2026-07-26.md`.
