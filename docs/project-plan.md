# Extraction plan and checkpoints

## Objective and evidence

Build an independent, reusable, installable Python framework from a private
source repository without exporting operational records or project-specific
policy.

Completion is proven only by the checks recorded in the final verification
report. Passing unit tests alone is not sufficient evidence for installation,
migration, restore, portability, privacy, or use by independent consumers.

## Risk baseline

- The source worktree had 386 pre-existing entries when extraction began.
- The focused source suite ran 145 tests: 143 passed and two integration tests
  failed because pre-existing demo deletions broke repository references.
- Source `memory_audit.py`, the adaptive execution validator, the sensitive
  content scanner, and syntax validation of 18 Draft 2020-12 schemas passed
  independently.
- Exported source paths in the current allow-list are attributable to
  `dev-torugo`. The project owner confirmed ownership and authority for the
  initial public source publication on 2026-07-26.

## Phase 0 — boundary and rights

Checkpoint:

- provisional name: StateWeave;
- local destination selected by the operator: `Documents/StateWeave`;
- licensing direction: source-available, non-commercial, separate commercial
  license;
- initial public GitHub repository creation and push were authorized on
  2026-07-26; definitive licensing, tags, releases, and package publication
  remain on hold;
- exact allow-list and transformation records live in the provenance manifest.

## Phase 1 — memory-core vertical slice

Required evidence:

1. clean package installation in a temporary environment;
2. CLI initialization and audit of a synthetic project;
3. official Draft 2020-12 validation, including negative keyword coverage;
4. reciprocal `supersedes` and `superseded_by`;
5. deterministic backlinks, conflicts, TTL, and review queue;
6. atomic writes and exclusive writer lock;
7. migration dry-run, migration with backup, and restore into a clean target;
8. core scan with no source-project, fixed-authority, model, path, or runtime
   coupling.

## Phase 2 — optional modules

- workflow: work requests, handoffs, acceptances, roles, and authority;
- orchestration: task slices, routing, input manifests, receipts, evaluations,
  and execution graphs;
- runtime: portable lifecycle and writer-lease interfaces;
- telemetry: local allow-listed observations and read-only reporting;
- adapters: runtime-specific integrations, including Codex;
- policy packs: project-owned roles, floors, retention, and directory layout.

Each module must be independently installable or importable without making it
a core dependency.

## Phase 3 — consumers, CI, and documentation

- two independent synthetic repositories;
- supported Python and operating-system CI matrix;
- architecture, quickstart, configuration, extension, security, privacy, and
  versioning guides;
- adversarial tests for path traversal, input drift, stale locks, partial
  writes, schema downgrades, content leakage, and relationship cycles.

## Phase 4 — final gate

- reproducible clean installation;
- full tests and static checks;
- migration and restore drill;
- source-project neutrality and sensitive-content scans;
- provenance completeness;
- final report of commands, failures, limitations, pending decisions, and
  publication gates.

Definitive licensing, additional remotes, tags, releases, package publication,
or a maturity claim beyond proven evidence requires a separate human gate.

## Post-extraction continuity checkpoints — 2026-07-27

The continuity slice described in
`docs/technical-assessment-2026-07-27.md` landed through PR #1 at `c3cf585`
after all ten hosted matrix/static jobs passed. It provides deterministic
retrieval and context, idempotent candidates, persistent
episodes/receipts/evaluations, governed write-back, crash recovery, content
policy, derived indexing, concurrency tests, and a versioned benchmark.

The Gate E host boundary landed through PR #2 at `4f347b1` after the hosted
matrix exposed and then verified a Windows writer-lock remediation. It
provides:

- immutable Codex session preparation bound to policy, manifest, worker, and
  exact context;
- explicit approval references without adapter-granted authority;
- host-reported effect, receipt, and evaluation reconciliation;
- closed-world adapter audit and persistent orchestration binding;
- CLI and integration guidance.

The current repository-native slice addresses adoption and upstream capture:

- a read-only, hash-bound diagnostic for existing projects;
- single-sidecar materialization without overwriting existing content;
- embedded/sidecar config discovery with ambiguity and symlink rejection;
- versioned adapter-neutral capture requests and immutable envelopes;
- linear source checkpoints, replay-safe candidate ingestion, and capture
  audit;
- a synthetic proof from existing project through promotion and second-session
  retrieval.

These checkpoints do not expand publication authorization. Tags, releases,
package publication, definitive licensing, support, and maturity claims retain
their existing human gates.
