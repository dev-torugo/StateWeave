---
name: stateweave-onboarding
description: Guide safe onboarding of an existing project into StateWeave and review its Candidate Inbox through the local CLI. Use when Codex must plan or apply a tracked, local, or deferred StateWeave sidecar; list or filter memory candidates; preview a candidate; promote or reject one candidate with explicit human confirmation; or audit and back up onboarding evidence.
---

# StateWeave Onboarding

Use the bundled CLI bridge for deterministic commands. Keep the conversation as
guidance only: never persist the user's prompt, chat, raw logs, credentials, or
project contents as onboarding evidence.

## Project onboarding

1. Run `scripts/stateweave_onboarding.py onboarding-plan` with the project
   path, identity, and one explicit `--sidecar-policy` value:
   `tracked`, `local`, or `defer`.
2. Present the returned status, states, risks, decisions, actions, and
   `plan_sha256`. Explain that `local` does not edit ignore rules.
3. Stop if the plan is blocked. Ask the human to resolve the reported conflict
   outside this skill.
4. Do not apply until the human explicitly confirms the exact plan and policy.
5. Run `onboarding-apply` with the reviewed digest, reviewer role, timestamp,
   and `--confirm-human`.
6. Run `audit-onboarding`, `audit-adoption`, and `audit` after application.

Never edit `AGENTS.md`, `.gitignore`, Git configuration, remotes, branches,
commits, tags, or releases. Never infer the sidecar policy from repository
contents.

## Candidate Inbox

1. Run `candidate-list` with only the filters the human requested.
2. Treat `effective_situation` as the current situation; `stored_status` is
   historical storage state.
3. Run `candidate-preview <id>` immediately before any decision.
4. Show changed fields, current/proposed hashes, findings, effective situation,
   and `preview_sha256`.
5. Ask for an explicit decision for that candidate only.
6. For promotion, run `promote-candidate` with
   `--expected-preview-sha256` and `--confirm-human`.
7. For rejection, select one bounded `--reason-code`, then run
   `reject-candidate` with the same preview digest and `--confirm-human`.
8. Run `audit-continuity` after decisions. Use `backup` before a batch when the
   human requests a recovery point.

Do not batch-confirm candidates. Refresh the preview whenever a digest mismatch
is reported. A rejection is immutable and bound to the exact candidate hash;
do not replace or edit its file.

## Command bridge

Run:

```bash
python3 scripts/stateweave_onboarding.py <stateweave-command> [arguments]
```

Read `references/cli-contract.md` when constructing exact command lines or
interpreting statuses. Use no shell interpolation and pass paths as separate
arguments.
