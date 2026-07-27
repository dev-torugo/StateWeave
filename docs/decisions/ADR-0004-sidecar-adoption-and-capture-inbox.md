# ADR-0004 — Existing projects use a sidecar and review-only capture inbox

**Status:** accepted for current development

## Context

`stateweave init` correctly refuses non-empty destinations, but that leaves an
existing software project without a low-risk adoption path. Reusing generic
root names such as `memory/` could collide with project content, while
automatically scanning source files would cross privacy, provenance, and trust
boundaries.

The continuity pipeline already governs `MemoryCandidate` promotion. What is
missing is a versioned, incremental host-ingestion boundary before candidates.

## Decision

- Existing projects receive one fixed `.stateweave-project/` sidecar.
- Adoption is read-only by default and returns a hash-bound plan with
  `safe`, `blocked`, or `already_adopted`.
- Apply requires the exact plan digest, timestamp, and explicit confirmation.
- Sidecar construction uses a same-directory staging store and rename.
- Existing project filenames contribute only to an inventory digest and count;
  project file contents are not scanned or copied.
- CLI configuration discovery accepts one embedded or one sidecar config and
  rejects ambiguity and symlinks.
- Hosts submit versioned `CaptureRequest` documents with linear source cursors.
- StateWeave persists immutable `CaptureEnvelope` records and checkpoints.
- Every captured event becomes an idempotent, review-required candidate.
- Capture never promotes memory, executes a source adapter, or grants
  authority.

## Consequences

- Existing projects remain byte-preserved outside one sidecar namespace.
- Removing or relocating the sidecar is an explicit operator decision; the
  tool does not edit VCS ignore rules.
- Backup covers StateWeave state but not unrelated host-project files.
- Crash replay can finish partial candidate/envelope/checkpoint persistence;
  audits expose incomplete bindings before recovery.
- A concrete Git, filesystem, CI, or runtime adapter still requires separate
  authorization, privacy rules, fixtures, and compatibility evidence.
