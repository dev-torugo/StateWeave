# ADR-0002: Official Draft 2020-12 validation

- Status: accepted
- Date: 2026-07-25

## Context

The extracted core had packaged Draft 2020-12 schemas and semantic graph
checks, but runtime callers could omit schema validation. A partial or optional
validator would not prove closed properties, conditional rules, formats, or
other schema keywords.

## Decision

Use `jsonschema>=4.23,<5` as the sole production dependency and invoke
`Draft202012Validator` by default for:

- parsed project configuration;
- facts, decisions, and state during repository audit;
- facts and decisions before atomic mutation;
- records checked after migration.

Keep cross-record references, reciprocal supersession, cycles, TTL policy,
backlinks, and conflicts in semantic validators. Register deterministic
standard-library format checks for RFC 3339 datetimes, ISO dates, and absolute
URIs rather than adding format-specific production packages.

## Consequences

- Invalid records fail before mutation and remain invalid during audit.
- Packaged schemas become executable runtime contracts and package data.
- Consumer-supplied validator hooks may add checks but cannot bypass the
  official schemas.
- Future schema changes require compatibility notes, negative tests, and an
  explicit migration when previously accepted data becomes invalid.
