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
