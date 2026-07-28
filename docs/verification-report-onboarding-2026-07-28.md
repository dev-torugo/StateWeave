# Onboarding and Candidate Inbox verification — 2026-07-28

## Scope

This checkpoint covers the repo-local, skills-only Codex plugin, hash-bound
onboarding plans, explicit sidecar disposition, per-candidate review, immutable
rejection evidence, backup/restore, and concurrent decision safety. All test
projects, candidates, prompts, and records were synthetic.

It does not claim a published plugin, universal marketplace, MCP integration,
automatic project scanning, production maturity, or measured model value.

## Local evidence

The repository gate completed with:

```text
153 tests passed
backup_created: true
migration_status: complete
restored_ok: true
StateWeave repository gate: OK
```

Focused evidence includes:

- a read-only skill bridge call that produced a deterministic
  `OnboardingPlan` while preserving all host files;
- fixed-option pending decisions for ready, deferred, blocked, and complete
  plan states, with no free-form prompt or chat content;
- immutable sidecar-policy conflicts, including a later request to defer, fail
  closed instead of masking the recorded disposition;
- apply refusal without the exact plan digest and human confirmation;
- tracked, local, and deferred sidecar policy behavior;
- onboarding plan/policy audit after backup and clean-target restore;
- adversarial audit binding plan, nested adoption, policy, configured project,
  and reviewer role;
- Candidate Inbox list/filter and effective-state derivation;
- mandatory preview digests for both promotion and rejection;
- immutable rejection replay and tamper detection;
- interrupted-promotion effects require promotion reconciliation and cannot be
  followed by rejection;
- concurrent promotion and rejection with exactly one durable winner;
- continuity audit after the concurrent decision.

## Plugin ingestion

The source plugin and its skill passed the official local validators:

```text
Plugin validation passed: plugins/stateweave-onboarding
Skill is valid!
```

An isolated Codex CLI installation test added the repo-local catalog
`stateweave-local`, installed
`stateweave-onboarding@stateweave-local`, and confirmed:

```text
version: 0.1.0
installed: true
enabled: true
source: local repo marketplace entry
```

The disposable CLI configuration was removed after the check. No personal or
universal marketplace was created. The same versioned marketplace metadata is
the handoff surface for the Codex app; no live GUI assertion is made here.

## Preserved boundaries

- no edit to host `AGENTS.md`, `.gitignore`, Git configuration, or VCS state;
- no raw prompt, chat, reasoning, credential, project content, or log stored;
- no MCP server, watcher, app manifest, hook, or external adapter;
- no production dependency, license, tag, release, or package publication;
- `stateweave.core` remains runtime-neutral; the optional lock-reuse argument
  is generic and defaults to the existing safe locked behavior.
