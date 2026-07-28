# Codex value experiment

## Status and boundary

`scripts/run_codex_value_experiment.py` is an experimental host runner. It is
not part of `stateweave.core`, not a supported runtime adapter, and not a
general Codex launcher.

The runner uses an existing QGIS/Python project only as an immutable source
baseline. It reads one allow-listed text module,
`caixa_ferramentas_interface/domain/risk_calculations.py`, and copies that
module text into a fresh temporary directory for every run. It never commits,
and it never copies
the source `.git`, outputs, QGIS projects, databases, spreadsheets, PDFs,
images, archives, reports, caches, or sidecars.

Every run verifies the source module hash again. The caller should additionally
hash the complete source tree before and after a campaign when byte-for-byte
baseline evidence is required.

## Arms and tasks

The fixed experiment has four arms:

- `none`: no memory;
- `full`: all 100 compact synthetic memory items, capped at 64 KiB;
- `bundle`: the canonical `ContextBundle`, capped at eight items and 12,000
  item bytes;
- `projection`: an experimental projection of that bundle which preserves
  record identity, revision, score, reasons, sources, and the source bundle
  hash.

It exercises three NumPy-only tasks: shape validation, the valid class domain,
and output dtype. Each task starts from the same allow-listed implementation
with a deterministic synthetic defect. Acceptance tests use only generated
NumPy arrays and do not require QGIS.

The prompt declares retrieved content to be untrusted evidence. The exact
prompt context hash is stored in the input manifest; the projection is not a
new public StateWeave contract.

## Safe dry run

The default is a deterministic fake executor. It proves fixture isolation,
task evaluation, receipt/evaluation reconciliation, and all audits without
contacting Codex:

```bash
PYTHONPATH=src python3 scripts/run_codex_value_experiment.py \
  --source-project /path/to/existing-project \
  --repetitions 1
```

Run the real pilot only in a locally authenticated Codex environment:

```bash
PYTHONPATH=src python3 scripts/run_codex_value_experiment.py \
  --source-project /path/to/existing-project \
  --execute \
  --model gpt-5.6-sol \
  --repetitions 1 \
  --output /tmp/stateweave-codex-pilot.json
```

One repetition is the 12-run pilot: three tasks by four arms. After reviewing
isolation, receipts, token ceilings, and failures, use three repetitions for
the 36-run campaign. Arms run sequentially in a balanced rotating order.

## Persisted evidence

The aggregate output contains only allow-listed evidence:

- the locally observed `codex-cli` semantic version and a hash of that bounded
  version response (stderr and all other free text are discarded);
- input, cached input, output, and reasoning token counts;
- uncached input, total duration, first-event and first-message latency;
- event type counts and process/test exit status;
- prompt, context, manifest, workspace, session, and source hashes;
- changed paths and line-count statistics;
- acceptance-test result and memory/continuity/Codex audit booleans.

The JSONL stream is parsed in memory. Event content, stdout messages, stderr,
prompts, transcripts, and reasoning are discarded. Monetary cost is `null`
because ChatGPT-authenticated `codex exec` reports tokens but not a dependable
currency charge.

Each run has a 15-minute timeout and a 150,000-input-token ceiling. A campaign
stops after one million pilot input tokens. These controls are experiment
limits, not product policy.

## Decision gate

The report evaluates:

- best memory arm succeeds in at least eight of nine full-campaign runs;
- memory gains at least two successes over no memory;
- canonical bundle loses no more than one success against full memory;
- median canonical-bundle input is at most 70% of full memory;
- projection loses no more than one success against the canonical bundle and
  uses at most 80% of its input tokens;
- every receipt/evaluation/audit and privacy boundary remains valid.

A failed gate is evidence to improve relevance, projection, or concurrency
before expanding product claims. Dry-run results validate the harness only;
they are not evidence of model value.
