# Security model

## Trust boundary

Memory projects and backup archives are untrusted local inputs. StateWeave does
not treat a valid JSON record as authorization for an external action.
Network, credential, cloud, billing, publication, or customer effects belong
outside memory-core and require a separately authorized adapter.

## Filesystem controls

- configured paths must remain under the resolved project root;
- existing-project adoption writes only a fixed sidecar after a hash-bound
  dry-run, rejects symlinks/ambiguity, and rechecks the host inventory under
  an adoption lock;
- record identifiers determine filenames through a closed kind mapping;
- record reads are bounded by configured byte limits;
- writes use a temporary sibling, flush, `fsync`, and `os.replace`;
- multi-record writes use before/after payloads and a durable phase journal;
- idempotency keys are stored only as SHA-256 digests;
- optimistic record hashes reject stale writers;
- a writer owns an unpredictable token in an atomically created lock directory;
- waiting writers avoid repeatedly opening owner evidence, and release retries
  only transient sharing violations within the configured lock timeout;
- a stale lock is reported but never stolen automatically; recovery requires
  the observed owner digest, token, stale policy, and explicit confirmation;
- canonical record areas reject nested directories, non-JSON entries, special
  files, and symlinks instead of silently ignoring them;
- restore validates member names, types, sizes, hashes, duplicates, and the
  exact allow-list before writing;
- restore writes into an empty destination by default and never uses
  `extractall`.

## Schema and semantics

The vertical-slice contract assigns record shape, keywords, formats, and closed
properties to `jsonschema>=4.23,<5` using the official Draft 2020-12 validator.
Configuration loading, repository audit, record writes, and post-migration
audit invoke the packaged schemas by default. Standard-library format checkers
assert RFC 3339 datetimes, ISO dates, and absolute URIs without adding another
production dependency.

Semantic audit handles only relationships that require the complete record
set: role membership, references, reciprocal supersession, cycles, TTL policy,
backlinks, and structured conflicts.

## Content controls

Valid JSON is still untrusted data. The optional content-policy boundary runs a
bounded traversal at candidate ingress, promotion, mutation-plan handling, and
retrieval. Capture request ingestion, Codex session preparation, and
observation ingress use the same boundary. The built-in baseline:

- blocks obvious credential assignments and private-key markers;
- never includes the matched credential value in an error;
- warns when text resembles an instruction to override host/system authority;
- lets a project replace the inspector without changing memory-core.

Every `ContextBundle` marks content as `evidence_only` and
`treat_content_as_untrusted: true`. A warning does not authorize execution.
The baseline is defense in depth, not a complete secret, PII, malware, or
natural-language prompt-injection detector.

## Reporting vulnerabilities

The public development repository still has no reviewed vulnerability
disclosure channel. Before any release or support claim, add a reviewed
`SECURITY.md` containing supported versions, private reporting coordinates,
response expectations, and disclosure policy. Do not place personal contact
data in examples or tests.

## Known limits

- Local filesystem permissions and disk encryption remain the operator's
  responsibility.
- `fsync` and atomic replacement do not protect against every filesystem,
  hardware, or physical disk failure mode.
- Structured conflict detection compares declared claim keys and values; it is
  not natural-language truth inference.
- The Codex bridge prepares and reconciles documents but does not launch,
  secure, independently attest, or authorize a host runtime. An approval
  reference is a host-supplied evidence locator, not proof verified by
  StateWeave.
- In-memory telemetry avoids implicit persistence but cannot enforce a
  consumer's downstream handling.
- The current project has not completed hosted multi-process stress testing on
  every target operating system.
- The derived index verifies every source hash and remains O(n) in record
  count; it is an optimization cache, not a signed integrity boundary.
- Capture source identifiers, locators, cursors, and events are host claims;
  StateWeave checks consistency and content but does not attest their origin.
