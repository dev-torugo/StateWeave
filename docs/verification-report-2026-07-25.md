# Verification report — 2026-07-25

> This report records the local pre-publication checkpoint. The project owner
> later authorized the public development repository on 2026-07-26; see
> `docs/publication-report-2026-07-26.md` for the publication boundary and
> current gates.

## Scope and outcome

This report covers the local StateWeave extraction candidate. It is a
development build (`0.0.0.dev0`), not a beta, stable release, definitive
license, published package, or OSI open-source claim.

The locally verifiable framework criteria are implemented: memory-core,
official Draft 2020-12 validation, optional workflow/orchestration/runtime/
telemetry/adapters/policy packages, synthetic consumers, package build, clean
installation, migration, backup, restore, and documentation.

## Commands and observed evidence

### Canonical repository gate

```text
PYTHON_BIN=<Python 3.12.13> bash scripts/check.sh
```

Observed:

- extraction, neutrality, sensitive-content, provenance, licensing-hold, schema,
  and CI contracts: passed;
- unit, negative, integration, and adversarial suite: 57 tests, 0 failures;
- release drill: one legacy record migrated, journal status `complete`, backup
  created, clean restore audited with two records;
- final gate message: `StateWeave repository gate: OK`.

### Static and syntax checks

```text
PYTHONPYCACHEPREFIX=<temporary path> python3 -m compileall -q src scripts tests
ruff format --check src tests scripts
ruff check src tests scripts
mypy --python-version 3.11 src/stateweave
bash -n scripts/check.sh
```

Observed:

- compile step: passed with the temporary bytecode cache;
- formatting: 40 Python files already formatted;
- lint: no findings;
- typing: 25 source files, no issues;
- shell syntax: passed.

### Distribution build

```text
UV_CACHE_DIR=<temporary path> uv build --out-dir <temporary build directory>
```

Observed:

- `stateweave-0.0.0.dev0.tar.gz`: built;
- `stateweave-0.0.0.dev0-py3-none-any.whl`: built;
- core and all optional-module JSON schemas were present in the wheel.

### Clean installation and CLI smoke

A new Python 3.12.13 virtual environment installed the wheel and fetched only
the declared production dependency graph rooted at
`jsonschema>=4.23,<5`.

Executed from outside the source tree:

```text
stateweave --help
stateweave init <temporary project> --id clean-install --name "Clean Install"
stateweave audit --config <temporary project> --json
```

Observed:

- console entry point loaded from the wheel;
- initialization succeeded;
- official packaged schemas loaded;
- audit returned `ok: true`, one state record, and zero errors;
- optional module schemas and the passive Codex adapter imported from the
  installed wheel.

### Migration and restore

```text
PYTHONPATH=src <Python 3.12.13> scripts/run_release_drill.py
```

Observed:

```json
{
  "backup_created": true,
  "migration_changes": 1,
  "migration_status": "complete",
  "restored_ok": true,
  "restored_records": 2
}
```

### Synthetic consumers

`tests/test_synthetic_consumers.py` copied `research-lab` and `service-team`
into separate temporary roots and ran the complete official-schema plus
semantic audit. Both contained three records and deterministic backlinks.
Their configuration and policy packs differ and neither imports the other.

## Adversarial coverage

The executed suite includes:

- POSIX, Windows-style, URI-like, and ZIP-member path traversal;
- non-finite JSON and oversized/count-limited inputs;
- symlink records;
- stale, exclusive, and incorrectly released writer locks;
- partial restore and migration failures with rollback checks;
- schema downgrade, unknown version, closed-property, format, uniqueness,
  conditional, and required-field failures;
- reciprocal supersession, dangling references, cross-kind edges, and large
  relationship cycles;
- orchestration DAG cycles, missing edges, manifest drift, receipt time
  reversal, and dangling evaluations;
- workflow lifecycle mismatch, duplicate identifiers, unknown roles, and human
  gate bypass attempts;
- disabled, non-allow-listed, sensitive, nested, multiline, non-finite, and
  over-retention telemetry inputs;
- high-confidence secret, email-like data, absolute user path, source-project,
  fixed-authority, runtime, and concrete-model scans at their applicable
  boundaries.

## Failures encountered and disposition

- The first bytecode compilation attempt used the macOS global bytecode cache,
  which the sandbox denied. Re-running with `PYTHONPYCACHEPREFIX` in a temporary
  writable directory passed.
- The first isolated build could not access the default `uv` cache and then
  could not resolve the build backend without network access. Re-running with a
  temporary cache and approved network access built both distributions.
- The first clean wheel installation could not resolve `jsonschema` while
  network access was restricted. Re-running with approved network access
  installed the declared dependency and passed the smoke checks.
- The first extracted source-distribution gate found that the CI workflow was
  absent from the sdist. `MANIFEST.in` was corrected to include the pinned
  workflow; the distribution was rebuilt before final validation.
- At extraction start, the focused source-repository suite reported 143/145:
  two integration failures referenced demo files already deleted in the
  pre-existing source worktree. Independent source memory, orchestration,
  sensitive-content, and schema checks passed. StateWeave does not change or
  mask those source-repository failures.

## Limitations and pending human gates

- The Linux/macOS/Windows × Python 3.11–3.13 CI matrix is versioned but has not
  run on a hosted service because no remote exists. No operating system is
  declared officially supported yet.
- Ownership and publication authority were later confirmed by the project
  owner on 2026-07-26.
- `LICENSING-PROPOSAL.md` is explicitly not a license. Definitive legal text and
  contribution terms require legal and human approval.
- StateWeave remains a provisional name pending trademark review.
- At this historical checkpoint, the final export allow-list still required
  human review and no remote, push, tag, artifact upload, release commit,
  publication, customer communication, cloud provisioning, or live runtime
  dispatch had been performed.
- The Codex adapter is passive preparation only; it does not attest the host
  runtime.
- Telemetry is intentionally in-memory and has no persistence, dashboard,
  exporter, or network transport.
- Archive tests cover targeted hostile cases, not exhaustive fuzzing on every
  target OS.

## Next gates

1. Obtain legal review of definitive source-available terms.
2. Complete trademark and naming review.
3. Run the checked-in CI matrix on the public remote
   and record hosted evidence before declaring OS support.
4. Request separate approval for any tag, package upload, or release.
