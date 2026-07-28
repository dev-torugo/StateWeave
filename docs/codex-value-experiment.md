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

It exercises three NumPy-only synthetic regressions identified to the agent
only by opaque maintenance IDs. The objective and query do not disclose the
missing project decision. Relevant memory facts bind the opaque ID to the
contract, while distractors use neutral identifiers and expose no
ground-truth labels.

The acceptance evaluator is held outside the disposable agent workspace and
runs only after Codex exits. It loads the modified module with
`python -B -c` and checks generated NumPy cases for non-identical but
broadcastable dimensions, domain boundaries and an alternate sentinel, and
output representation plus input immutability. Only the evaluator source
hash, exit code, duration, and pass/fail result are retained. NumPy must
already be available in the selected target environment; the experimental
runner does not add it as a StateWeave production dependency. Clean package
test environments without NumPy skip only execution-fixture tests while still
exercising source isolation, context bounds, the CLI boundary, timeout
handling, and JSONL sanitization.

Before a campaign, a mandatory preflight proves for all three IDs that the
mutant fails, the deterministic repair passes, the agent workspace contains no
test or evaluator, the objective and query remain opaque, the bundle retrieves
all four incident-bound facts, and full context omits relevance/topic labels.
The prompt still declares retrieved content to be untrusted evidence. The
exact prompt context hash is stored in the input manifest; the projection is
not a new public StateWeave contract.

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

One repetition is the 12-run feasibility pilot: three tasks by four arms. It
can never pass the value gate. After reviewing isolation, preflight, receipts,
token ceilings, and failures, use three repetitions for the preregistered
36-run campaign. Arms run sequentially in a balanced rotating order.

## Persisted evidence

The aggregate output contains only allow-listed evidence:

- the locally observed `codex-cli` semantic version and a hash of that bounded
  version response (stderr and all other free text are discarded);
- input, cached input, output, and reasoning token counts;
- uncached input, total duration, first-event and first-message latency;
- event type counts and process/test exit status;
- prompt, context, manifest, workspace, session, and source hashes;
- changed paths and line-count statistics;
- held-out evaluator hash/result and memory/continuity/Codex audit booleans.

The JSONL stream is parsed in memory. Event content, stdout messages, stderr,
prompts, transcripts, and reasoning are discarded. Monetary cost is `null`
because ChatGPT-authenticated `codex exec` reports tokens but not a dependable
currency charge.

Each run has a 15-minute timeout and a 150,000-input-token ceiling. A campaign
stops after one million pilot input tokens. These controls are experiment
limits, not product policy.

## Decision gate

The primary comparison is preregistered as canonical `bundle` versus `none`;
the runner does not choose the best memory arm after seeing results. Passing
requires:

- real execution with exactly three repetitions, all 36 unique cells, no
  token-cap stop, and a passing preflight;
- canonical bundle success in at least eight of nine runs and at least two of
  three runs for every incident family;
- a one-sided exact binomial result at or below 0.05 for discordant paired
  bundle-versus-none outcomes, with more bundle-only wins than none-only wins;
- median uncached canonical-bundle input at most 70% of full-memory input;
- every receipt/evaluation/audit and minimal evaluator-evidence boundary to
  remain valid.

Full memory and the experimental projection remain secondary diagnostics.
Projection success and its uncached-token ratio are reported but cannot make
the primary gate pass.

A failed gate is evidence to improve relevance, projection, or concurrency
before expanding product claims. Dry-run results validate the harness only;
they are not evidence of model value. The evaluator is operationally held out
from the agent workspace, not a cryptographic secret from an agent that could
somehow discover and inspect the harness repository itself.
