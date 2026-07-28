# ADR-0005 — Onboarding is plan-bound and candidate decisions are per item

**Status:** accepted for current development

## Context

The sidecar adoption and capture contracts provide safe primitives but do not
define a conversational onboarding sequence, sidecar tracking intent, a
filterable Candidate Inbox, or durable rejection evidence.

## Decision

- Represent onboarding as a versioned, hash-bound plan with explicit states,
  risks, decisions, and actions.
- Require the operator to choose `tracked`, `local`, or `defer`.
- Persist applied plans and one immutable sidecar policy decision inside the
  StateWeave extension boundary.
- Never edit host instructions, ignore files, or VCS state.
- Derive candidate situation from candidate, canonical record, and immutable
  decision evidence.
- Require a fresh preview digest and explicit human confirmation for every
  promotion or rejection.
- Keep promotion on the existing durable transaction path.
- Store rejection as an immutable decision bound to the exact candidate hash,
  with a bounded reason code and no free-form conversation.
- Package the conversational workflow as a repo-local, skills-only Codex
  plugin with one repo-local marketplace entry and no personal/universal
  marketplace, MCP, app, or hook configuration.

## Consequences

Onboarding can explain risk without inspecting host content. Local sidecars
remain the operator's VCS responsibility. Deferred onboarding has no store in
which to persist a plan. Backup and restore include applied onboarding and
candidate decision evidence through the existing extension boundary.
