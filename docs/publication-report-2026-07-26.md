# Public repository publication gate — 2026-07-26

> Historical publication evidence. The continuity work added locally on
> 2026-07-27 has not been committed, pushed, released, or covered by a new
> hosted CI run in this report.

## Authorization

The project owner directly confirmed ownership of the product and authority to
publish the transformed StateWeave source. The same gate approved:

- one public GitHub repository at `dev-torugo/StateWeave`;
- an independent initial history on `main`;
- publication of the reviewed development source snapshot;
- execution of the checked-in GitHub Actions matrix.

## Publication boundary

This is a public development snapshot, not a software release. The
authorization does not include:

- a definitive license or permission grant;
- a tag, GitHub Release, package registry upload, or downloadable release
  artifact;
- contribution licensing terms;
- a beta, stable, production-ready, or officially supported OS claim;
- publication of the private source repository or any excluded record class.

StateWeave remains a provisional name. `LICENSING-PROPOSAL.md` remains a
proposal and is not a license.

## Export boundary

The final export remains limited by
`docs/provenance/TRANSFORMATION-MANIFEST.json`. It excludes operational
records, prompts, chats, telemetry snapshots, private evidence, customer data,
credentials, PII, raw logs, and project-specific implementation.

## Verification

Local pre-publication evidence:

- `PYTHON_BIN=<Python 3.12.13> bash scripts/check.sh`: extraction contracts
  passed, 57 tests passed, and the migration/backup/restore drill completed;
- Ruff 0.12.12: 40 files formatted and no lint findings;
- mypy 1.20.2: 25 source files checked with no findings;
- Python bytecode compilation: passed with an isolated cache;
- source distribution and wheel: built in a temporary directory;
- clean Python 3.12.13 environment: installed the wheel, loaded the CLI,
  initialized a synthetic project, and audited it with `ok: true`.

No built artifact was uploaded. Repository visibility, initial commit, push,
and the hosted CI result remain GitHub-side publication evidence. Hosted CI is
required before any operating-system support statement.

The first pre-publication lint attempt resolved Ruff 0.16.0 under the prior
broad dependency range and enabled rules beyond the verified lint contract.
The development dependency was pinned to Ruff 0.12.12, the version used by the
verified checkpoint, and all static checks were then repeated successfully.

The first hosted run passed quality/build plus every Linux and macOS matrix
job, but all three Windows jobs exposed a platform-ordering defect: path
resolution raised `PathBoundaryError` before the official schema could return
the documented `ConfigurationError`. Configuration loading now performs
official schema validation before platform-specific path resolution, with
regression coverage for both slash styles.

## Remaining gates

1. Qualified legal review and adoption of definitive source-available terms.
2. Inbound contribution terms.
3. Trademark and naming review.
4. Separate approval for tags, releases, artifacts, or package publication.
