# Changelog

## Unreleased

- Added an immutable Codex host bridge with policy-aware session preparation,
  explicit approval references, content-bound dispatch, host-reported
  receipt/evaluation reconciliation, effect observations, and adapter audit.
- Added deterministic query, budgeted `ContextBundle` compilation, content
  warnings, conflicts, revision hashes, and a rebuildable derived index.
- Added idempotent candidate capture, human-gated promotion, persistent
  workflow/orchestration episodes, context-bound receipts/evaluations, and
  evidence-bound `MutationPlan` write-back for facts, decisions, and state.
- Added durable mutation journals, optimistic SHA-256 preconditions,
  idempotency receipts, abrupt-process recovery, and fingerprint-bound stale
  lock recovery commands.
- Made writer-lock polling and release resilient to transient Windows sharing
  violations without weakening ownership checks or stale-lock recovery.
- Made record and extension layouts fail closed on unexpected entries and
  included opaque extension artifacts in verified backup/restore.
- Added bounded content-policy hooks, obvious-secret blocking, persistent
  instruction warnings, multiprocess concurrency tests, and a versioned
  context benchmark.
- Began local clean-room extraction.
- Defined the memory-core vertical-slice contract.
- Integrated official Draft 2020-12 validation into configuration, audit,
  mutation, and migration verification.
- Added optional workflow, orchestration, runtime, telemetry, adapter, and
  policy-pack modules with synthetic contract tests.
- Added a pinned Linux, macOS, and Windows CI matrix for Python 3.11–3.13.
- Recorded source-available licensing intent without applying a license.
- Authorized an initial public GitHub development snapshot while retaining the
  definitive-license, trademark, release, and package-publication gates.
- Pinned the verified Ruff version so the public CI cannot silently widen its
  lint contract when a new tool release appears.
- Made configuration schema validation precede platform-specific path
  resolution so POSIX and Windows traversal inputs fail with the same public
  configuration error.
