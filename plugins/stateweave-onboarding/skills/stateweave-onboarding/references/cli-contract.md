# CLI contract

## Plugin installation boundary

This plugin is distributed only through the repository catalog named
`stateweave-local` at `.agents/plugins/marketplace.json`. Add the repo-local
marketplace explicitly, install `stateweave-onboarding@stateweave-local`, and
start a new thread. Do not create or update a personal marketplace.

## Onboarding

- `onboarding-plan TARGET --id ID --name NAME --sidecar-policy POLICY`
  performs no write. Exit 1 means `blocked`; other plan states exit 0.
- `onboarding-apply TARGET --id ID --name NAME --sidecar-policy POLICY
  --expected-plan-sha256 SHA --decided-at TIME --reviewer-role ROLE
  --confirm-human` applies only the exact reviewed plan.
- `tracked` records intent to version the sidecar. `local` records intent to
  keep it local but never edits ignore rules. `defer` creates no sidecar.

## Candidate Inbox

- `candidate-list --config ROOT` accepts `--situation`, `--classification`,
  `--confidence`, `--operation`, `--source-type`, and
  `--review-required`/`--no-review-required`.
- `candidate-preview ID --config ROOT` returns `preview_sha256`.
- `promote-candidate ID --config ROOT --expected-preview-sha256 SHA
  --reviewer-role ROLE --promoted-at TIME --confirm-human` uses the existing
  durable promotion path.
- `reject-candidate ID --config ROOT --expected-preview-sha256 SHA
  --reason-code CODE --reviewer-role ROLE --decided-at TIME --confirm-human`
  stores one immutable decision. Reason codes are bounded and contain no free
  text.

Effective situations include `pending`, `promoted`, `rejected`,
`promotion-needs-reconciliation`, `blocked-current-record`,
`blocked-missing-record`, `blocked-stale-revision`, `blocked-target`, and
`blocked-rejection-drift`.

## Evidence and recovery

Run `audit-onboarding`, `audit-adoption`, `audit-continuity`, and `audit` after
mutations. Standard `backup` and `restore` include onboarding plans, sidecar
policy, candidates, and rejection decisions because they are extension
artifacts.
