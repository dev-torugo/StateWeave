# Performance and retrieval evaluation

## Local evaluation contract

`scripts/benchmark_context.py` creates temporary, fully synthetic projects. It
does not read an existing StateWeave project or any operational record.

`--sizes` is a total canonical record count. The total includes the generated
`STATE-current`, so `--sizes 100` creates 99 facts plus one state record. Each
size run measures:

- full semantic audit;
- canonical context scan;
- derived index build;
- verified-index context compilation;
- one durable idempotent fact mutation after the sized measurements.

The result reports the initial count as `records` and the post-mutation count
as `records_after_mutation`. It also requires the scan and indexed bundles to
be byte-for-byte equivalent.

The same invocation runs a retrieval-quality evaluation whose corpus size is
configurable with `--evaluation-size` and defaults to 1,000 canonical records.
It contains one state record, ten topics, four relevant facts and two hard
negatives per topic, plus neutral fillers and no relationship edges. The 30
queries each have four explicit relevant judgments and two topic-specific
hard negatives. A hard negative shares the unique terms and common lexical
terms but omits the exact objective phrase.

For both canonical scan and verified index, the output records:

- macro recall at 1, 4, and 8;
- macro precision at 8, using 8 as the denominator;
- mean reciprocal rank (MRR);
- canonical JSON bytes for all selected items;
- canonical JSON bytes for the complete bundle;
- per-query retrieved IDs, hard-negative ranks, bytes, and latency.

Duplicate retrieved IDs are counted once by the metric helper. Evaluation
fails if scan and index produce different bundles.

## Concurrency harness

The local thread harness runs the following workload shapes independently for
canonical scan and verified-index access:

- one reader;
- four readers;
- eight readers;
- seven readers and one writer.

Every worker executes `--concurrency-operations` operations, which defaults to
one. Workers start from a barrier. The harness reports total completed
operations per wall-clock second and reader/writer p50, p95, minimum, and
maximum end-to-end latency. Latency includes time waiting for the existing
exclusive project lock. Both access paths use the same canonical snapshot;
the run fails unless every concurrent reader and both scan/index phases return
the same context bundle ID.

The mixed writer calls the normal durable mutation API with an expected
revision and a unique idempotency key, overwriting `STATE-current` with
byte-identical content. This creates a real transaction and lock contender
without changing the canonical snapshot or invalidating the verified index.
It does not represent changing-record ingestion, index refresh, multi-process
contention, or a write-throughput ceiling.

The harness uses local Python threads and one temporary filesystem. Its
throughput and percentiles are observations of that process, fixture,
filesystem, interpreter, and run only. They are not scalability evidence,
capacity guidance, SLOs, or cross-platform claims.

## Running it

The default size matrix remains 100, 1,000, and 10,000 total records. A
10-record smoke with one operation per worker is:

```bash
PYTHONPATH=src python3 scripts/benchmark_context.py \
  --sizes 10 \
  --repeats 1 \
  --evaluation-size 101 \
  --concurrency-size 10 \
  --concurrency-operations 1
```

`--concurrency-size` defaults to the first value in `--sizes`. Retrieval
quality defaults to a fixed 1,000-record corpus; callers may select a smaller
corpus of at least 61 total records for smoke validation. The canonical unit
suite uses 101 evaluation records and the small invocation above; the default
1,000-record quality corpus and 10,000-record scale row remain deliberate
operator-run workloads.

## Current synthetic fixture result

On the 2026-07-28 audited local run of the default 1,000-record retrieval
corpus, both scan and index produced identical quality and byte results:

| Metric | Scan | Index |
|---|---:|---:|
| recall@1 | 0.25 | 0.25 |
| recall@4 | 1.0 | 1.0 |
| recall@8 | 1.0 | 1.0 |
| precision@8 | 0.5 | 0.5 |
| MRR | 1.0 | 1.0 |
| selected-item bytes per query, mean | 10,309.333 | 10,309.333 |
| complete-bundle bytes per query, mean | 11,093 | 11,093 |

The four relevant records ranked first through fourth and both hard negatives
ranked fifth and sixth. The reported quality gate requires recall@8 of at
least 0.80, precision@8 of at least 0.50, and MRR of at least 0.70. Scan/index
bundle equivalence is a separate mandatory invariant. This synthetic fixture
passed the gate and invariant, but the values do not establish real-world
retrieval quality or semantic understanding.

## Historical single-reader timing

The earlier 2026-07-27 local timing used the version 1 size semantics, where
`--sizes 100,1000` actually created 101 and 1,001 records including state.
The measurements below remain historical observations of those actual counts;
they were not rerun through the broader version 2 harness.

| Actual records including state | Audit p95 | Context scan p95 | Index build | Indexed context p95 | Mutation |
|---:|---:|---:|---:|---:|---:|
| 101 | 71.9 ms | 138.6 ms | 143.0 ms | 65.6 ms | 149.4 ms |
| 1,001 | 450.4 ms | 1,002.8 ms | 1,166.1 ms | 612.4 ms | 1,310.9 ms |

For the historical 1,001-record run, verified-index p95 was about 39% below
scan p95 while producing the same bundle. The index still hashes every
canonical source and validates cached record schemas, so lookup remains
linear in the number of records. It avoids repeated JSON parsing and the full
semantic graph audit; it is not a database or inverted-index complexity
claim.

## Remaining evaluation work

- rerun the full size matrix with version 2 semantics and retained environment
  metadata;
- separate cold and warm filesystem-cache measurements;
- measure actual changing-record writes and explicit index rebuilds;
- add process-level contention on supported operating systems;
- evaluate denser graphs, overlapping topics, and graded relevance;
- decide whether a future metadata-only index can reduce hashing and parsing
  while retaining the same fail-closed guarantees.
