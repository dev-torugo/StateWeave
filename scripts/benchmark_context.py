#!/usr/bin/env python3
"""Reproducible local benchmark for audit, retrieval, index, and mutation."""

from __future__ import annotations

import argparse
import json
import math
import platform
import statistics
import sys
import time
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Callable

from stateweave.context import build_context_index, compile_context
from stateweave.core.audit import audit_repository
from stateweave.core.io import atomic_write_json
from stateweave.core.project import initialize_project, put_record


def synthetic_fact(identifier: str, index: int) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "kind": "fact",
        "id": identifier,
        "title": f"Synthetic indexed item {index}",
        "statement": f"Synthetic indexed context item {index} is available.",
        "status": "verified",
        "domain": "synthetic",
        "fact_class": "general",
        "recorded_at": "2026-07-20T12:00:00Z",
        "verified_at": "2026-07-20T12:00:00Z",
        "review_after": "2026-08-15",
        "confidence": "high",
        "owner_role": "maintainer",
        "classification": "internal",
        "sources": [
            {
                "uri": "https://example.invalid/benchmark",
                "title": "Synthetic benchmark source",
                "accessed_at": "2026-07-20T12:00:00Z",
                "kind": "primary",
            }
        ],
        "claim": {
            "subject": "benchmark-service",
            "predicate": "availability",
            "scope": "synthetic",
            "object": "stable",
        },
        "references": [],
        "supersedes": [],
        "superseded_by": None,
    }


def query() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "kind": "memory_query",
        "objective": "recover indexed context item",
        "as_of": "2026-07-27",
        "terms": ["indexed", "context"],
        "filters": {
            "record_kinds": ["fact"],
            "statuses": ["verified"],
            "domains": ["synthetic"],
            "classifications": ["internal"],
            "minimum_confidence": "high",
        },
        "relation_depth": 0,
        "budget": {"max_items": 8, "max_content_bytes": 12000},
    }


def timed(operation: Callable[[], Any], repeats: int) -> tuple[list[float], Any]:
    durations: list[float] = []
    result: Any = None
    for _ in range(repeats):
        started = time.perf_counter()
        result = operation()
        durations.append(time.perf_counter() - started)
    return durations, result


def summary(samples: list[float]) -> dict[str, float]:
    ordered = sorted(samples)
    p95_index = max(0, math.ceil(len(ordered) * 0.95) - 1)
    return {
        "p50_ms": round(statistics.median(ordered) * 1000, 3),
        "p95_ms": round(ordered[p95_index] * 1000, 3),
        "min_ms": round(ordered[0] * 1000, 3),
        "max_ms": round(ordered[-1] * 1000, 3),
    }


def benchmark_size(size: int, repeats: int) -> dict[str, Any]:
    with TemporaryDirectory() as temporary:
        config = initialize_project(
            Path(temporary) / "memory",
            project_id="benchmark-project",
            project_name="Synthetic Benchmark Project",
        )
        setup_started = time.perf_counter()
        for index in range(size):
            payload = synthetic_fact(f"FCT-benchmark-{index:08d}", index)
            atomic_write_json(config.facts_dir / f"{payload['id']}.json", payload)
        setup_seconds = time.perf_counter() - setup_started

        audit_samples, audit = timed(
            lambda: audit_repository(config, today=date(2026, 7, 27)),
            repeats,
        )
        if not audit.ok:
            raise RuntimeError(f"benchmark fixture is invalid: {audit.errors}")
        scan_samples, scanned = timed(
            lambda: compile_context(config, query()),
            repeats,
        )
        build_started = time.perf_counter()
        build_context_index(config, as_of=date(2026, 7, 27))
        build_seconds = time.perf_counter() - build_started
        indexed_samples, indexed = timed(
            lambda: compile_context(config, query()),
            repeats,
        )
        if scanned != indexed:
            raise RuntimeError("indexed context differs from canonical scan")

        mutation = synthetic_fact("FCT-benchmark-mutation", size)
        mutation_started = time.perf_counter()
        put_record(
            config,
            mutation,
            idempotency_key=f"benchmark-mutation-{size}",
        )
        mutation_seconds = time.perf_counter() - mutation_started
        return {
            "records": size + 1,
            "setup_seconds": round(setup_seconds, 6),
            "audit": summary(audit_samples),
            "context_scan": summary(scan_samples),
            "index_build_ms": round(build_seconds * 1000, 3),
            "context_indexed": summary(indexed_samples),
            "mutation_ms": round(mutation_seconds * 1000, 3),
            "selected_items": indexed["usage"]["selected_items"],
            "context_bytes": indexed["usage"]["content_bytes"],
        }


def parse_sizes(value: str) -> list[int]:
    try:
        sizes = [int(item) for item in value.split(",")]
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "sizes must be comma-separated integers"
        ) from exc
    if not sizes or any(size < 1 for size in sizes):
        raise argparse.ArgumentTypeError("sizes must be positive integers")
    return sizes


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sizes", type=parse_sizes, default=[100, 1000, 10000])
    parser.add_argument("--repeats", type=int, default=5)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    payload = {
        "schema_version": 1,
        "kind": "stateweave_context_benchmark",
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
        },
        "repeats": args.repeats,
        "results": [benchmark_size(size, args.repeats) for size in args.sizes],
    }
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
