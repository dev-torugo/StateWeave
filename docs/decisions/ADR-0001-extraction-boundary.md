# ADR-0001 — Clean extraction boundary

- Status: accepted
- Date: 2026-07-25
- Decision owner: human project owner

## Decision

Implement a clean, runtime-neutral core and transform only allow-listed code,
schemas, tests, and public-facing concepts. Do not copy internal facts,
decisions, work records, handoffs, acceptances, execution receipts, telemetry
snapshots, pricing evidence, prompts, chats, raw logs, customer artifacts, or
credentials.

Project-specific authority, directory names, retention, capability floors, and
runtime surfaces become configuration, adapters, or policy packs.

## Consequences

The new history starts without copying source commits. Authorship and
file-level transformations are preserved in a machine-readable manifest. The
project owner confirmed ownership and authorized an initial public GitHub
development snapshot on 2026-07-26.

No definitive license is adopted. Tags, releases, package publication,
contribution terms, and maturity claims remain blocked on their separate human
and legal gates. The StateWeave name remains provisional pending trademark
review.
