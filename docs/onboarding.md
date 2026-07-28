# Human-gated onboarding and Candidate Inbox

## Boundary

Onboarding is an explicit conversation over deterministic CLI records. It does
not inspect project file contents, collect prompts or chats, edit `AGENTS.md` or
`.gitignore`, or run Git commands. The only project mutation for a new host is
the fixed `.stateweave-project/` sidecar already governed by the adoption
contract.

The repo-local Codex plugin lives at `plugins/stateweave-onboarding`. It
contains one skill and a subprocess bridge to the installed StateWeave CLI. It
has no MCP server, app, hook, credential, or global configuration.

## Local Codex plugin installation

The versioned repo-local catalog is
`.agents/plugins/marketplace.json`, named `stateweave-local`. It contains one
local entry for `./plugins/stateweave-onboarding`. It is not a personal,
universal, or remotely published marketplace.

From the repository root, configure this explicit local marketplace and
install its snapshot:

```bash
codex plugin marketplace add ./.agents/plugins
codex plugin add stateweave-onboarding@stateweave-local
codex plugin list
```

The marketplace must be added explicitly because non-default repo-local
catalogs are not discovered as personal marketplaces. Start a new Codex thread
after installation so skill discovery uses the installed snapshot.

During local plugin development, use the `plugin-creator` cachebuster helper
and reinstall from `stateweave-local`; do not edit the marketplace or Codex
configuration by hand.

## OnboardingPlan

Create a read-only plan:

```bash
stateweave onboarding-plan ./existing-project \
  --id existing-project \
  --name "Existing Project" \
  --sidecar-policy tracked
```

The Draft 2020-12 `OnboardingPlan` embeds the exact adoption plan and exposes:

- current states, including deployment and content-inspection state;
- bounded risks;
- explicit decisions;
- pending decision codes with fixed options and human-confirmation requirements;
- ordered actions and their mutation/confirmation requirements;
- `ONP-<sha256>` identity and `plan_sha256`.

The semantic validator also binds the nested adoption digest and project
identity, requires contiguous stable actions, and enforces the pending decision
set for each plan status. An immutable recorded policy takes precedence over a
later request to defer onboarding.

The sidecar policy is always supplied by the operator:

| Policy | Meaning |
|---|---|
| `tracked` | record the operator's intent to version the sidecar |
| `local` | record local-only intent without editing ignore or VCS settings |
| `defer` | create nothing and leave continuity unavailable |

Apply only the reviewed digest:

```bash
stateweave onboarding-apply ./existing-project \
  --id existing-project \
  --name "Existing Project" \
  --sidecar-policy tracked \
  --expected-plan-sha256 <sha256> \
  --decided-at 2026-07-27T20:00:00Z \
  --reviewer-role maintainer \
  --confirm-human
```

Application recomputes the plan. Drift fails closed. A tracked or local
application uses the existing atomic adoption path, then stores the reviewed
plan and one immutable, hash-bound sidecar policy decision under the extension
directory. A deferred plan has no StateWeave store in which evidence could be
persisted and therefore performs no mutation.

## Candidate Inbox

List candidates without loading their full proposed records into the summary:

```bash
stateweave candidate-list \
  --config ./existing-project \
  --situation pending \
  --confidence high
```

Filters are available for effective situation, classification, confidence,
operation, source type, and review requirement. `stored_status` is historical;
`effective_situation` is derived from immutable rejection evidence, promotion
state, canonical record existence, and current/expected/proposed hashes.

Every promotion or rejection requires a fresh preview:

```bash
stateweave candidate-preview CND-<digest> --config ./existing-project
```

Use the returned `preview_sha256` for exactly one candidate decision:

```bash
stateweave promote-candidate CND-<digest> \
  --config ./existing-project \
  --expected-preview-sha256 <sha256> \
  --reviewer-role maintainer \
  --promoted-at 2026-07-27T20:10:00Z \
  --confirm-human
```

```bash
stateweave reject-candidate CND-<digest> \
  --config ./existing-project \
  --expected-preview-sha256 <sha256> \
  --reason-code out-of-scope \
  --reviewer-role maintainer \
  --decided-at 2026-07-27T20:10:00Z \
  --confirm-human
```

Rejection uses a bounded reason code, contains no free-form conversation, and
is immutable and bound to the exact candidate and preview hashes. Rejection is
refused when the proposed canonical effect already exists after an interrupted
promotion; the promotion path must reconcile that state. The existing durable
candidate promotion remains the only promotion path.

## Audit, backup, and restore

Run:

```bash
stateweave audit-onboarding --config ./existing-project
stateweave audit-adoption --config ./existing-project
stateweave audit-continuity --config ./existing-project
stateweave audit --config ./existing-project
```

`audit-onboarding` verifies closed-world paths, plan schemas/digests, policy
digests, configured reviewer roles, and bidirectional plan/policy/adoption/
project identity bindings. `audit-continuity` additionally checks candidate
rejection schemas, filenames, candidate hashes, and promotion/rejection
conflicts.

Onboarding plans, the sidecar policy decision, candidates, and rejection
decisions are extension artifacts. Standard backup and clean-target restore
preserve them and verify their member hashes before extraction.

The synthetic gate and isolated plugin-ingestion evidence are recorded in
`docs/verification-report-onboarding-2026-07-28.md`.
