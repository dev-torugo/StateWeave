# Continuity implementation verification — 2026-07-27

## Scope and epistemic boundary

This report covers the local uncommitted worktree based on revision
`75ed9f33caebc601803e61ff52142020f3ba5544`. It does not claim a commit, push,
hosted CI run, tag, release, package publication, definitive license, trademark
clearance, or officially supported operating system.

The earlier `docs/technical-assessment-2026-07-27.md` is the diagnostic input.
This document records the implemented response and local evidence.

## Implemented closure

| Finding | Current implementation evidence |
|---|---|
| F-01 retrieval | `stateweave.context.query_memory`, CLI `query`, structured filters, lexical score/reasons, graph expansion |
| F-02 context compiler | deterministic hash-bound `ContextBundle`, byte/item budgets, warnings, conflicts, revisions, exclusion counts |
| F-03 capture/write-back | idempotent candidates, human promotion, persistent receipt/evaluation episodes, `MutationPlan` for fact/decision/state |
| F-04 abrupt durability | phase journal, before/after payloads, real subprocess exit test, explicit rollback command |
| F-05 unexpected entries | closed-world canonical layout and extension traversal; audit/backup/migration fail closed |
| F-06 concurrency | optimistic SHA-256, idempotency digests, lock inspection/recovery, consistent exclusive snapshots, multiprocess tests |
| F-07 persistent ledger | atomic episode files, context-bound receipts, semantic combined audit, backup/restore round-trip |
| F-08 content trust | replaceable bounded policy hook, secret blocking, instruction warnings, evidence-only bundle label |
| F-09 agent UX | CLI `remember`, `query`, `context`, index, episode, plan, lock, and transaction commands |
| F-10 artifact provenance | candidate revision/tree/path/artifact hash/selector/time/method/observer/derivation contract |
| F-11 documentation drift | current/historical publication wording and continuity/security/performance guides synchronized |
| F-12 legal gate | intentionally open; no license, release, or external adoption claim added |

## Local evidence

The canonical command is:

```text
bash scripts/check.sh
```

After the implementation and documentation/provenance synchronization, the
gate passed with:

- extraction contracts: OK;
- 93 unit, integration, concurrency, negative, and adversarial tests: OK;
- migration/backup/restore release drill: OK.

Additional local quality and packaging checks passed:

- Ruff 0.12.12: 56 Python files formatted and no lint findings;
- mypy 1.20.2: no findings in 34 source files;
- Python bytecode compilation: OK;
- isolated sdist and wheel build: OK;
- clean wheel installation, CLI audit, optional-module imports, and packaged
  Draft 2020-12 schema checks: OK.

Focused evidence also passed for:

- a real subprocess killed after the first record in a two-record batch;
- fingerprint-bound stale lock recovery followed by journal rollback;
- five concurrent processes replaying one idempotency key into one receipt;
- six independent processes serializing distinct writes without lost updates;
- scan/index bundle equality and index invalidation after record drift;
- secret blocking without echoing the value and persistent-instruction warning;
- a two-session decision recovery without a known record ID;
- receipt/evaluation/MutationPlan write-back surviving verified backup/restore.

The local 100/1,000 benchmark and limitations are recorded in
`docs/performance.md`.

## Remaining external and maturity gates

- The current changes need separately authorized commit/push workflow and a
  hosted Python 3.11–3.13 Linux/macOS/Windows matrix before support claims.
- The default lexical retriever has no semantic embedding recall claim.
- The verified index remains O(n) because it checks every source hash.
- There is no automatic Git/filesystem/CI/runtime watcher; hosts invoke capture
  explicitly and provide observed provenance.
- Legal licensing, inbound contribution terms, and trademark review remain
  human/external gates.
