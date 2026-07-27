# Performance and scale

## Benchmark contract

`scripts/benchmark_context.py` creates only temporary synthetic projects and
measures:

- full semantic audit;
- canonical context scan;
- derived index build;
- verified-index context compilation;
- one durable idempotent mutation.

It checks that scan and indexed bundles are byte-for-byte equivalent. The
default size matrix is 100, 1,000, and 10,000 records; callers can use a
smaller smoke matrix:

```bash
PYTHONPATH=src python3 scripts/benchmark_context.py \
  --sizes 100,1000,10000 \
  --repeats 5
```

The canonical test suite executes a 10-record, two-repeat smoke only. The
larger matrix is intentionally not part of every unit-test run.

## Local baseline — 2026-07-27

Observed on Python 3.12.3 under WSL2 Linux 6.18.33.2, three repeats. These are
local indicative measurements, not hosted or multiplatform SLO evidence.

| Records including state | Audit p95 | Context scan p95 | Index build | Indexed context p95 | Mutation |
|---:|---:|---:|---:|---:|---:|
| 101 | 71.9 ms | 138.6 ms | 143.0 ms | 65.6 ms | 149.4 ms |
| 1,001 | 450.4 ms | 1,002.8 ms | 1,166.1 ms | 612.4 ms | 1,310.9 ms |

For 1,001 records, verified-index p95 was about 39% below scan p95 while
producing the same bundle. The index still hashes every canonical source and
validates cached record schemas, so lookup remains O(n). It avoids repeated
JSON parsing and full semantic graph audit; it is not a database or an
inverted-index complexity claim.

The 10,000-record row was not executed during this local checkpoint and must
not be inferred. The script supports it for a dedicated run with sufficient
time and hosted evidence.

## Working targets, not support claims

For the current local 1,000-record development envelope, the measured working
targets are:

- full audit p95 below 600 ms;
- verified-index context p95 below 750 ms;
- canonical scan context p95 below 1.2 s;
- one durable mutation below 1.5 s.

These thresholds are regression indicators for a comparable machine and
fixture. They are not promises for other filesystems, dense graphs, larger
records, concurrent writers, operating systems, or hardware.

## Remaining performance work

- run 10,000-record sparse and dense graphs;
- separate cold filesystem cache from warm cache;
- measure index update rather than full rebuild;
- measure reader/writer contention on Linux, macOS, and Windows;
- add recall/precision fixtures for lexical ranking;
- decide whether a future metadata-only index can reduce hashing/parsing while
  retaining the same fail-closed guarantees.
