# Security model

## Trust boundary

Memory projects and backup archives are untrusted local inputs. StateWeave does
not treat a valid JSON record as authorization for an external action.
Network, credential, cloud, billing, publication, or customer effects belong
outside memory-core and require a separately authorized adapter.

## Filesystem controls

- configured paths must remain under the resolved project root;
- record identifiers determine filenames through a closed kind mapping;
- record reads are bounded by configured byte limits;
- writes use a temporary sibling, flush, `fsync`, and `os.replace`;
- a writer owns an unpredictable token in an atomically created lock directory;
- a stale lock is reported but never stolen automatically;
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

## Reporting vulnerabilities

No public disclosure channel exists while the project remains local. Before
publication, add a reviewed `SECURITY.md` containing supported versions,
private reporting coordinates, response expectations, and disclosure policy.
Do not place personal contact data in examples or tests.

## Known limits

- Local filesystem permissions and disk encryption remain the operator's
  responsibility.
- Atomic replacement does not make a multi-file backup durable against
  physical disk failure.
- Structured conflict detection compares declared claim keys and values; it is
  not natural-language truth inference.
- The passive runtime adapter prepares documents but does not secure or attest
  a host runtime.
- In-memory telemetry avoids implicit persistence but cannot enforce a
  consumer's downstream handling.
- The current project has not completed hosted multi-process stress testing on
  every target operating system.
