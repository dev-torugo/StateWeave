# Privacy and data minimization

## Core content

Memory-core stores the facts, decisions, state summaries, and source metadata a
consumer explicitly writes. It does not collect prompts, chats,
chain-of-thought, runtime thread identifiers, raw logs, token usage, customer
records, or credentials.

The optional continuity store persists only documents the consumer explicitly
submits: candidates, context bundles, workflow/orchestration episodes,
receipts, evaluations, and mutation plans. It does not capture a host session
or transcript automatically. Runtime and model identifiers may appear only in
receipt observations. Candidate idempotency keys are represented by digests,
not stored in raw form.

Projects should keep content classifications and retention rules appropriate
to their own jurisdiction and operating context. Configuration supports an
allow-list of classifications but does not replace a privacy program.
The baseline content inspector blocks a narrow class of obvious secrets; it is
not evidence that accepted content contains no PII, regulated data, or less
obvious credentials.

## Extraction boundary

The clean extraction excludes all source operational records, execution
receipts, telemetry snapshots, private evidence, customer data, photos,
projects, backups, credentials, PII, banking information, and signed
agreements. Synthetic examples use reserved `.invalid` URLs and invented
organizations.

## Optional telemetry

The telemetry module is not a core requirement. Its public contract is opt-in,
local, bounded, metadata-only, and allow-listed. It accepts short scalar fields,
rejects sensitive field names, nested objects, non-finite numbers, multiline
strings, and fields absent from policy. Missing observations remain missing;
free text and native runtime payloads are not copied wholesale.

Telemetry persistence and transport are intentionally absent. A consumer that
adds them owns retention, deletion, access, export, and jurisdictional policy.

## Publication gate

Before publication, run both a sensitive-content scanner and a positive export
allow-list audit. Absence of a scanner finding is weaker evidence than proving
that every released path belongs to the allow-list.
