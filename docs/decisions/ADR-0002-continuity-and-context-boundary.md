# ADR-0002 — Continuity, retrieval, and authority boundary

- Status: accepted for the current development worktree
- Date: 2026-07-27
- Decision owner: project owner through the continuity implementation goal

## Decision

StateWeave remains a memory and continuity control plane. It does not become a
model host or autonomous external-effect executor.

The terms used by the implementation are:

- **continuous**: knowledge can cross sessions through durable, auditable
  records and receipts; it does not mean automatic transcript capture;
- **candidate**: untrusted proposed memory with provenance and an idempotency
  digest, not canonical knowledge;
- **promoted**: a candidate or mutation plan has passed schema, semantic,
  content-policy, revision, and applicable human gates and was committed by a
  durable core transaction;
- **retrieved**: a record matched explicit filters/ranking against one
  hash-bound snapshot;
- **optimized**: a deterministic bundle stays within an explicit content
  budget and may use a verified derived index; it does not imply semantic
  embeddings, constant-time lookup, or a model-specific tokenizer;
- **receipt**: observed execution evidence bound to an input manifest and
  stored context digest; it is not inferred success;
- **authority**: remains with project policy, the host, and explicit human
  approval. Retrieved text is evidence-only data.

`stateweave.core` owns only canonical memory, configuration, schema/semantic
validation, durable mutation, locks, backup, restore, and migration. Context
retrieval, indexing, content inspection, persistent episodes, candidates, and
write-back live in optional modules that depend inward on core.

Project-specific roles, classifications, TTLs, paths, limits, and authority
remain versioned configuration or policy. Runtime-specific identifiers remain
adapter/receipt observations and do not enter core policy.

## Trust boundaries

1. Capture adapters and humans submit untrusted candidate data.
2. Schema and content policy may reject it before persistence.
3. Promotion and mutation plans require explicit revision/idempotency evidence
   and applicable human approval.
4. Context compilation labels all selected content untrusted and
   `evidence_only`, even when the canonical record is verified.
5. Runtime adapters may prepare or observe execution but never grant external
   authority merely because a document validates.

## Consequences

- The core stays usable by two independent synthetic consumers and does not
  import the optional modules.
- Context results are reproducible for the same query, `as_of`, configuration,
  and record snapshot.
- The derived index can be deleted and rebuilt without losing canonical data.
- Workflow and orchestration documents become durable only through the
  continuity store; their standalone validators remain side-effect free.
- No automatic Git, filesystem, CI, network, or runtime watcher is claimed.
  Hosts must explicitly invoke capture and provide observed provenance.
- Lexical ranking and a byte-budget token heuristic are known limitations.
