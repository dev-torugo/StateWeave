# StateWeave working agreements

## Scope

- Treat StateWeave as a provisional name until a trademark gate is complete.
- A public GitHub development repository was authorized by the project owner
  on 2026-07-26. Do not create additional remotes, tags, releases, package
  publications, or maturity claims without explicit human approval.
- Do not add a definitive software license. Licensing remains a proposal until
  legal and ownership review are complete.
- Keep examples synthetic. Do not copy operational records, private evidence,
  credentials, PII, customer data, receipts, prompts, chats, or raw logs from
  any source repository.

## Architecture

- `stateweave.core` must remain runtime-neutral and project-neutral.
- Project-specific roles, directories, TTLs, policies, and limits belong in
  versioned configuration.
- Runtime-specific behavior belongs in adapters. Concrete model identifiers
  may appear only in runtime observations and receipts, never in core policy.
- JSON records are validated with the official JSON Schema Draft 2020-12
  implementation. Semantic validators are reserved for cross-record
  invariants.

## Development

- Read `docs/project-plan.md` and the nearest relevant contract before editing.
- Preserve provenance in `docs/provenance/TRANSFORMATION-MANIFEST.json`.
- Prefer small changes with focused positive, negative, and adversarial tests.
- Ask before adding production dependencies, creating commits, or expanding
  supported external systems.
- Run `python -m unittest discover -s tests -v` before handoff.
