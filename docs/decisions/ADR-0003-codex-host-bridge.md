# ADR-0003 — Codex host bridge remains evidence-bound and non-authoritative

**Status:** accepted

**Date:** 2026-07-27

## Context

The passive `CodexAdapter` could translate a runtime-neutral dispatch envelope
but did not connect deterministic context, project policy, persistent
execution evidence, and continuity audit. Letting the adapter launch work or
infer a receipt would collapse the existing trust boundary and make
StateWeave responsible for authority it cannot verify.

## Decision

Add a runtime-specific bridge in `stateweave.adapters`, outside
`stateweave.core`, with two explicit phases:

1. preparation compiles and stores the exact `ContextBundle`, evaluates
   project policy, records host-supplied approval references, and persists an
   immutable session;
2. observation accepts a complete host-reported receipt and evaluation,
   reconciles their hashes and effects with the session, appends the existing
   orchestration ledger, and persists an immutable observation binding.

Every prepared dispatch retains `execution_authorized: false`. A
`ready_for_host` result means only that configured policy preconditions were
satisfied. Approval references are evidence locators, not independently
verified approvals.

Concrete implementation and model identifiers remain limited to receipt
observations. Session and observation JSON use packaged Draft 2020-12 schemas;
semantic validators enforce cross-document and cross-store invariants.

## Consequences

- Hosts get a directly consumable, persistent lifecycle without importing
  runtime concerns into memory-core.
- Missing or drifted evidence fails closed instead of being inferred.
- Replays converge by content hash and immutable IDs.
- Backup/restore covers the adapter artifacts through the generic extension
  path.
- StateWeave still does not execute, attest, or authorize Codex.
- A future live adapter must remain a separately authorized external-system
  integration and preserve this receipt boundary.
