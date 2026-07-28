#!/usr/bin/env python3
"""Reproducible local evaluation for audit, retrieval, and concurrency."""

from __future__ import annotations

import argparse
import json
import math
import platform
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Callable

from stateweave.context import (
    build_context_index,
    compile_context,
    inspect_context_index,
)
from stateweave.core.audit import audit_repository
from stateweave.core.config import ProjectConfig
from stateweave.core.io import (
    atomic_write_json,
    canonical_json_bytes,
    read_json,
    sha256_file,
)
from stateweave.core.project import initialize_project, put_record

AS_OF = date(2026, 7, 27)
RETRIEVAL_TOPIC_COUNT = 10
QUERIES_PER_TOPIC = 3
RELEVANT_RECORDS_PER_TOPIC = 4
HARD_NEGATIVES_PER_TOPIC = 2
RETRIEVAL_QUERY_COUNT = RETRIEVAL_TOPIC_COUNT * QUERIES_PER_TOPIC
RETRIEVAL_K_VALUES = (1, 4, 8)
MAX_RETRIEVAL_ITEMS = max(RETRIEVAL_K_VALUES)
MIN_EVALUATION_RECORDS = (
    1
    + RETRIEVAL_TOPIC_COUNT
    * (RELEVANT_RECORDS_PER_TOPIC + HARD_NEGATIVES_PER_TOPIC)
)
DEFAULT_EVALUATION_RECORDS = 1000
QUALITY_THRESHOLDS = {
    "recall_at_8": 0.80,
    "precision_at_8": 0.50,
    "mrr": 0.70,
}


@dataclass(frozen=True)
class RetrievalCase:
    """One synthetic query with explicit relevance and negative judgments."""

    identifier: str
    query: dict[str, Any]
    relevant_ids: tuple[str, ...]
    hard_negative_ids: tuple[str, ...]


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
        "as_of": AS_OF.isoformat(),
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


def retrieval_cases() -> list[RetrievalCase]:
    """Return three queries for each of ten synthetic topics."""

    cases: list[RetrievalCase] = []
    objective_templates = (
        "Recover synthetic {topic} {marker} signal",
        "Find synthetic {topic} {marker} evidence",
        "Review synthetic {topic} {marker} context",
    )
    for topic_index in range(RETRIEVAL_TOPIC_COUNT):
        topic = f"topic{topic_index:02d}x"
        marker = f"marker{topic_index:02d}y"
        relevant_ids = tuple(
            (
                f"FCT-retrieval-topic-{topic_index:02d}"
                f"-relevant-{record_index}"
            )
            for record_index in range(RELEVANT_RECORDS_PER_TOPIC)
        )
        hard_negative_ids = tuple(
            (
                f"FCT-retrieval-topic-{topic_index:02d}"
                f"-hard-negative-{record_index}"
            )
            for record_index in range(HARD_NEGATIVES_PER_TOPIC)
        )
        for query_index, template in enumerate(objective_templates):
            cases.append(
                RetrievalCase(
                    identifier=(
                        f"QRY-synthetic-topic-{topic_index:02d}"
                        f"-{query_index}"
                    ),
                    query={
                        "schema_version": 1,
                        "kind": "memory_query",
                        "objective": template.format(
                            topic=topic,
                            marker=marker,
                        ),
                        "as_of": AS_OF.isoformat(),
                        "terms": [topic, marker],
                        "filters": {
                            "record_kinds": ["fact"],
                            "statuses": ["verified"],
                            "domains": ["synthetic"],
                            "classifications": ["internal"],
                            "minimum_confidence": "high",
                        },
                        "relation_depth": 0,
                        "budget": {
                            "max_items": MAX_RETRIEVAL_ITEMS,
                            "max_content_bytes": 1024 * 1024,
                        },
                    },
                    relevant_ids=relevant_ids,
                    hard_negative_ids=hard_negative_ids,
                )
            )
    return cases


def _retrieval_records(
    cases: list[RetrievalCase],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    relevant: list[dict[str, Any]] = []
    hard_negatives: list[dict[str, Any]] = []
    for topic_index in range(RETRIEVAL_TOPIC_COUNT):
        topic_cases = cases[
            topic_index
            * QUERIES_PER_TOPIC : (topic_index + 1)
            * QUERIES_PER_TOPIC
        ]
        topic, marker = topic_cases[0].query["terms"]
        statements = " ".join(
            f"{case.query['objective']}." for case in topic_cases
        )
        for record_index, identifier in enumerate(
            topic_cases[0].relevant_ids
        ):
            target = synthetic_fact(identifier, topic_index)
            target["title"] = (
                f"Synthetic {topic} {marker} relevant evidence "
                f"{record_index}"
            )
            target["statement"] = statements
            target["claim"]["subject"] = (
                f"retrieval-topic-{topic_index:02d}"
                f"-relevant-{record_index}"
            )
            target["claim"]["object"] = (
                f"relevant-{topic_index:02d}-{record_index}"
            )
            relevant.append(target)

        for record_index, identifier in enumerate(
            topic_cases[0].hard_negative_ids
        ):
            negative = synthetic_fact(identifier, topic_index)
            negative["title"] = (
                f"Synthetic {topic} {marker} alternate signal "
                f"evidence context {record_index}"
            )
            negative["statement"] = (
                f"A candidate about {topic} and {marker} keeps recover, "
                "find, review, signal, evidence, and context separate."
            )
            negative["claim"]["subject"] = (
                f"retrieval-topic-{topic_index:02d}"
                f"-hard-negative-{record_index}"
            )
            negative["claim"]["object"] = (
                f"hard-negative-{topic_index:02d}-{record_index}"
            )
            hard_negatives.append(negative)
    return relevant, hard_negatives


def _retrieval_fillers(count: int) -> list[dict[str, Any]]:
    fillers: list[dict[str, Any]] = []
    for index in range(count):
        payload = synthetic_fact(
            f"FCT-retrieval-filler-{index:06d}",
            index,
        )
        payload["title"] = f"Synthetic neutral catalog item {index}"
        payload["statement"] = (
            f"Synthetic neutral catalog item {index} is available."
        )
        fillers.append(payload)
    return fillers


def _write_records(
    config: ProjectConfig,
    records: list[dict[str, Any]],
) -> None:
    for payload in records:
        atomic_write_json(config.facts_dir / f"{payload['id']}.json", payload)


def _initialize_sized_project(
    root: Path,
    total_records: int,
) -> ProjectConfig:
    """Create exactly ``total_records`` canonical records, including STATE."""

    config = initialize_project(
        root,
        project_id="benchmark-project",
        project_name="Synthetic Benchmark Project",
    )
    records = [
        synthetic_fact(f"FCT-benchmark-{index:08d}", index)
        for index in range(total_records - 1)
    ]
    _write_records(config, records)
    return config


def timed(operation: Callable[[], Any], repeats: int) -> tuple[list[float], Any]:
    durations: list[float] = []
    result: Any = None
    for _ in range(repeats):
        started = time.perf_counter()
        result = operation()
        durations.append(time.perf_counter() - started)
    return durations, result


def duration_summary(samples: list[float]) -> dict[str, float]:
    if not samples:
        raise ValueError("duration samples must not be empty")
    if any(
        sample < 0 or not math.isfinite(sample) for sample in samples
    ):
        raise ValueError("duration samples must be finite and non-negative")
    ordered = sorted(samples)
    p95_index = max(0, math.ceil(len(ordered) * 0.95) - 1)
    return {
        "p50_ms": round(statistics.median(ordered) * 1000, 3),
        "p95_ms": round(ordered[p95_index] * 1000, 3),
        "min_ms": round(ordered[0] * 1000, 3),
        "max_ms": round(ordered[-1] * 1000, 3),
    }


def byte_summary(samples: list[int]) -> dict[str, int | float]:
    if not samples:
        raise ValueError("byte samples must not be empty")
    if any(
        not isinstance(sample, int) or sample < 0 for sample in samples
    ):
        raise ValueError("byte samples must be non-negative integers")
    ordered = sorted(samples)
    p95_index = max(0, math.ceil(len(ordered) * 0.95) - 1)
    return {
        "total": sum(ordered),
        "mean": round(statistics.mean(ordered), 3),
        "p50": round(statistics.median(ordered), 3),
        "p95": ordered[p95_index],
        "min": ordered[0],
        "max": ordered[-1],
    }


def _validated_identifiers(
    outcome: dict[str, Any],
    field: str,
    *,
    allow_empty: bool,
) -> list[str]:
    value = outcome.get(field)
    if not isinstance(value, list) or any(
        not isinstance(identifier, str) or not identifier
        for identifier in value
    ):
        raise ValueError(f"{field} must be a list of non-empty identifiers")
    identifiers = list(dict.fromkeys(value))
    if not allow_empty and not identifiers:
        raise ValueError(f"{field} must contain at least one identifier")
    return identifiers


def retrieval_metrics(outcomes: list[dict[str, Any]]) -> dict[str, float]:
    """Compute macro retrieval metrics without duplicate-id inflation."""

    if not outcomes:
        raise ValueError("retrieval outcomes must not be empty")
    recalls = {size: [] for size in RETRIEVAL_K_VALUES}
    precision_at_8: list[float] = []
    reciprocal_ranks: list[float] = []
    for outcome in outcomes:
        relevant = set(
            _validated_identifiers(
                outcome,
                "relevant_ids",
                allow_empty=False,
            )
        )
        retrieved = _validated_identifiers(
            outcome,
            "retrieved_ids",
            allow_empty=True,
        )
        for size in RETRIEVAL_K_VALUES:
            recalled = relevant.intersection(retrieved[:size])
            recalls[size].append(len(recalled) / len(relevant))
        precise = relevant.intersection(
            retrieved[:MAX_RETRIEVAL_ITEMS]
        )
        precision_at_8.append(
            len(precise) / MAX_RETRIEVAL_ITEMS
        )
        first_rank = next(
            (
                rank
                for rank, identifier in enumerate(retrieved, start=1)
                if identifier in relevant
            ),
            None,
        )
        reciprocal_ranks.append(
            0.0 if first_rank is None else 1.0 / first_rank
        )
    return {
        "recall_at_1": round(statistics.mean(recalls[1]), 6),
        "recall_at_4": round(statistics.mean(recalls[4]), 6),
        "recall_at_8": round(statistics.mean(recalls[8]), 6),
        "precision_at_8": round(statistics.mean(precision_at_8), 6),
        "mrr": round(statistics.mean(reciprocal_ranks), 6),
    }


def quality_gate(metrics: dict[str, float]) -> dict[str, Any]:
    """Report retrieval-quality thresholds without changing ranking."""

    missing = sorted(set(QUALITY_THRESHOLDS) - set(metrics))
    if missing:
        raise ValueError(
            f"quality metrics are missing gate inputs: {missing}"
        )
    observed = {
        name: metrics[name] for name in QUALITY_THRESHOLDS
    }
    failed = [
        name
        for name, minimum in QUALITY_THRESHOLDS.items()
        if observed[name] < minimum
    ]
    return {
        "passed": not failed,
        "observed": observed,
        "failed_metrics": failed,
    }


def _evaluate_bundles(
    cases: list[RetrievalCase],
    bundles: list[dict[str, Any]],
    durations: list[float],
) -> dict[str, Any]:
    if len(cases) != len(bundles) or len(cases) != len(durations):
        raise ValueError(
            "retrieval cases, bundles, and durations must align"
        )
    outcomes: list[dict[str, Any]] = []
    item_bytes: list[int] = []
    bundle_bytes: list[int] = []
    for case, bundle, elapsed in zip(cases, bundles, durations):
        retrieved = [item["id"] for item in bundle["items"]]
        selected_bytes = sum(
            len(canonical_json_bytes(item)) for item in bundle["items"]
        )
        if selected_bytes != bundle["usage"]["content_bytes"]:
            raise RuntimeError("bundle item byte accounting drifted")
        complete_bytes = len(canonical_json_bytes(bundle))
        item_bytes.append(selected_bytes)
        bundle_bytes.append(complete_bytes)
        outcomes.append(
            {
                "query_id": case.identifier,
                "relevant_ids": list(case.relevant_ids),
                "hard_negative_ids": list(case.hard_negative_ids),
                "retrieved_ids": retrieved,
                "hard_negative_ranks": [
                    retrieved.index(identifier) + 1
                    for identifier in case.hard_negative_ids
                    if identifier in retrieved
                ],
                "item_bytes": selected_bytes,
                "bundle_bytes": complete_bytes,
                "latency_ms": round(elapsed * 1000, 3),
            }
        )
    return {
        "metrics": retrieval_metrics(outcomes),
        "latency": duration_summary(durations),
        "bytes": {
            "selected_items": byte_summary(item_bytes),
            "complete_bundle": byte_summary(bundle_bytes),
        },
        "queries": outcomes,
    }


def evaluate_retrieval(
    total_records: int = DEFAULT_EVALUATION_RECORDS,
) -> dict[str, Any]:
    """Evaluate the canonical scan and verified index on the same 30 queries."""

    if total_records < MIN_EVALUATION_RECORDS:
        raise ValueError(
            "retrieval evaluation requires at least "
            f"{MIN_EVALUATION_RECORDS} records including state"
        )
    cases = retrieval_cases()
    relevant, hard_negatives = _retrieval_records(cases)
    filler_count = (
        total_records - 1 - len(relevant) - len(hard_negatives)
    )
    fillers = _retrieval_fillers(filler_count)
    with TemporaryDirectory() as temporary:
        config = initialize_project(
            Path(temporary) / "memory",
            project_id="retrieval-evaluation",
            project_name="Synthetic Retrieval Evaluation",
        )
        _write_records(config, relevant + hard_negatives + fillers)
        audit = audit_repository(config, today=AS_OF)
        if not audit.ok or audit.record_count != total_records:
            raise RuntimeError(
                "retrieval fixture expected "
                f"{total_records} records, observed {audit.record_count}: "
                f"{audit.errors}"
            )

        scan_durations: list[float] = []
        scanned: list[dict[str, Any]] = []
        for case in cases:
            started = time.perf_counter()
            bundle = compile_context(config, case.query)
            scan_durations.append(time.perf_counter() - started)
            scanned.append(bundle)

        build_started = time.perf_counter()
        build_context_index(config, as_of=AS_OF)
        index_build_seconds = time.perf_counter() - build_started
        indexed_durations: list[float] = []
        indexed: list[dict[str, Any]] = []
        for case in cases:
            started = time.perf_counter()
            bundle = compile_context(config, case.query)
            indexed_durations.append(time.perf_counter() - started)
            indexed.append(bundle)
        if scanned != indexed:
            raise RuntimeError(
                "indexed evaluation differs from canonical scan"
            )

        scan_evaluation = _evaluate_bundles(
            cases,
            scanned,
            scan_durations,
        )
        index_evaluation = _evaluate_bundles(
            cases,
            indexed,
            indexed_durations,
        )
        scan_gate = quality_gate(scan_evaluation["metrics"])
        index_gate = quality_gate(index_evaluation["metrics"])
        return {
            "corpus": {
                "records_including_state": audit.record_count,
                "topics": RETRIEVAL_TOPIC_COUNT,
                "relevant_records": len(relevant),
                "hard_negative_records": len(hard_negatives),
                "filler_records": len(fillers),
                "relationship_edges": 0,
            },
            "query_count": len(cases),
            "ground_truth_judgments": sum(
                len(case.relevant_ids) for case in cases
            ),
            "scan_index_equivalent": True,
            "index_build_ms": round(index_build_seconds * 1000, 3),
            "quality_gate": {
                "passed": (
                    scan_gate["passed"] and index_gate["passed"]
                ),
                "thresholds": dict(QUALITY_THRESHOLDS),
                "scan": scan_gate,
                "index": index_gate,
            },
            "scan": scan_evaluation,
            "index": index_evaluation,
        }


def benchmark_size(size: int, repeats: int) -> dict[str, Any]:
    """Measure one exact initial record count, including STATE-current."""

    with TemporaryDirectory() as temporary:
        setup_started = time.perf_counter()
        config = _initialize_sized_project(
            Path(temporary) / "memory",
            size,
        )
        setup_seconds = time.perf_counter() - setup_started

        audit_samples, audit = timed(
            lambda: audit_repository(config, today=AS_OF),
            repeats,
        )
        if not audit.ok or audit.record_count != size:
            raise RuntimeError(
                f"benchmark fixture expected {size} records, "
                f"observed {audit.record_count}: {audit.errors}"
            )
        scan_samples, scanned = timed(
            lambda: compile_context(config, query()),
            repeats,
        )
        build_started = time.perf_counter()
        build_context_index(config, as_of=AS_OF)
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
            "records": size,
            "records_after_mutation": size + 1,
            "setup_seconds": round(setup_seconds, 6),
            "audit": duration_summary(audit_samples),
            "context_scan": duration_summary(scan_samples),
            "index_build_ms": round(build_seconds * 1000, 3),
            "context_indexed": duration_summary(indexed_samples),
            "mutation_ms": round(mutation_seconds * 1000, 3),
            "selected_items": indexed["usage"]["selected_items"],
            "context_bytes": indexed["usage"]["content_bytes"],
        }


def _concurrent_workload(
    config: ProjectConfig,
    *,
    access_path: str,
    readers: int,
    writers: int,
    operations_per_worker: int,
) -> dict[str, Any]:
    if readers < 1 or writers not in {0, 1}:
        raise ValueError(
            "workload requires readers >= 1 and zero or one writer"
        )
    if operations_per_worker < 1:
        raise ValueError("operations per worker must be positive")
    worker_count = readers + writers
    barrier = threading.Barrier(worker_count + 1)
    state_payload = read_json(
        config.state_file,
        max_bytes=config.limits.max_record_bytes,
    )
    state_revision = sha256_file(config.state_file)

    def read_worker() -> tuple[list[float], list[str]]:
        barrier.wait()
        samples: list[float] = []
        bundle_ids: list[str] = []
        for _ in range(operations_per_worker):
            started = time.perf_counter()
            bundle = compile_context(config, query())
            samples.append(time.perf_counter() - started)
            bundle_ids.append(bundle["id"])
        return samples, bundle_ids

    def write_worker() -> list[float]:
        barrier.wait()
        samples: list[float] = []
        for operation in range(operations_per_worker):
            started = time.perf_counter()
            put_record(
                config,
                state_payload,
                overwrite=True,
                expected_sha256=state_revision,
                idempotency_key=(
                    f"benchmark-{access_path}-same-state-{operation}"
                ),
            )
            samples.append(time.perf_counter() - started)
        return samples

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        reader_futures = [
            executor.submit(read_worker) for _ in range(readers)
        ]
        writer_futures = [
            executor.submit(write_worker) for _ in range(writers)
        ]
        wall_started = time.perf_counter()
        barrier.wait()
        reader_results = [
            future.result() for future in reader_futures
        ]
        writer_results = [
            future.result() for future in writer_futures
        ]
        wall_seconds = time.perf_counter() - wall_started

    reader_samples = [
        sample
        for samples, _ in reader_results
        for sample in samples
    ]
    writer_samples = [
        sample for samples in writer_results for sample in samples
    ]
    bundle_ids = {
        identifier
        for _, identifiers in reader_results
        for identifier in identifiers
    }
    if len(bundle_ids) != 1:
        raise RuntimeError(
            "concurrent readers returned different bundles"
        )
    context_bundle_id = next(iter(bundle_ids))
    operation_count = len(reader_samples) + len(writer_samples)
    return {
        "workload": (
            f"{readers}_readers"
            if writers == 0
            else f"{readers}_readers_1_writer"
        ),
        "readers": readers,
        "writers": writers,
        "operations_per_worker": operations_per_worker,
        "reader_operations": len(reader_samples),
        "writer_operations": len(writer_samples),
        "context_bundle_id": context_bundle_id,
        "wall_ms": round(wall_seconds * 1000, 3),
        "throughput_ops_per_second": round(
            operation_count / wall_seconds,
            3,
        ),
        "reader_latency": duration_summary(reader_samples),
        "writer_latency": (
            duration_summary(writer_samples)
            if writer_samples
            else None
        ),
    }


def benchmark_concurrency(
    size: int,
    operations_per_worker: int,
) -> dict[str, Any]:
    """Measure local thread contention for scan and verified-index readers."""

    paths: list[dict[str, Any]] = []
    with TemporaryDirectory() as temporary:
        config = _initialize_sized_project(
            Path(temporary) / "memory",
            size,
        )
        for access_path in ("scan", "index"):
            if access_path == "index":
                build_context_index(config, as_of=AS_OF)
                status = inspect_context_index(config, as_of=AS_OF)
                if not status["valid"]:
                    raise RuntimeError(
                        "concurrency index setup is invalid: "
                        f"{status['reason']}"
                    )
            workloads = [
                _concurrent_workload(
                    config,
                    access_path=access_path,
                    readers=readers,
                    writers=0,
                    operations_per_worker=operations_per_worker,
                )
                for readers in (1, 4, 8)
            ]
            workloads.append(
                _concurrent_workload(
                    config,
                    access_path=access_path,
                    readers=7,
                    writers=1,
                    operations_per_worker=operations_per_worker,
                )
            )
            index_valid_after = None
            if access_path == "index":
                index_valid_after = inspect_context_index(
                    config,
                    as_of=AS_OF,
                )["valid"]
                if not index_valid_after:
                    raise RuntimeError(
                        "same-content writer unexpectedly invalidated index"
                    )
            paths.append(
                {
                    "access_path": access_path,
                    "index_built": access_path == "index",
                    "index_valid_after": index_valid_after,
                    "workloads": workloads,
                }
            )
    bundle_ids = {
        workload["context_bundle_id"]
        for path in paths
        for workload in path["workloads"]
    }
    if len(bundle_ids) != 1:
        raise RuntimeError(
            "scan and indexed concurrency readers returned different bundles"
        )
    return {
        "records_including_state": size,
        "execution_model": "local_threads",
        "mixed_writer": "byte_identical_state_overwrite",
        "scan_index_equivalent": True,
        "access_paths": paths,
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


def positive_integer(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "value must be an integer"
        ) from exc
    if parsed < 1:
        raise argparse.ArgumentTypeError("value must be positive")
    return parsed


def evaluation_size(value: str) -> int:
    parsed = positive_integer(value)
    if parsed < MIN_EVALUATION_RECORDS:
        raise argparse.ArgumentTypeError(
            "evaluation size must be at least "
            f"{MIN_EVALUATION_RECORDS} records including state"
        )
    return parsed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--sizes",
        type=parse_sizes,
        default=[100, 1000, 10000],
        help=(
            "comma-separated total record counts, "
            "each including STATE-current"
        ),
    )
    parser.add_argument(
        "--repeats",
        type=positive_integer,
        default=5,
    )
    parser.add_argument(
        "--evaluation-size",
        type=evaluation_size,
        default=DEFAULT_EVALUATION_RECORDS,
        help=(
            "retrieval corpus total including state; "
            f"defaults to {DEFAULT_EVALUATION_RECORDS}"
        ),
    )
    parser.add_argument(
        "--concurrency-size",
        type=positive_integer,
        help=(
            "total records including state; "
            "defaults to the first --sizes value"
        ),
    )
    parser.add_argument(
        "--concurrency-operations",
        type=positive_integer,
        default=1,
        help="operations per worker; defaults to 1",
    )
    args = parser.parse_args()
    concurrency_size = args.concurrency_size or args.sizes[0]
    payload = {
        "schema_version": 2,
        "kind": "stateweave_context_evaluation",
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
        },
        "repeats": args.repeats,
        "size_semantics": "total_records_including_state",
        "results": [
            benchmark_size(size, args.repeats) for size in args.sizes
        ],
        "retrieval_evaluation": evaluate_retrieval(
            args.evaluation_size
        ),
        "concurrency": benchmark_concurrency(
            concurrency_size,
            args.concurrency_operations,
        ),
    }
    json.dump(
        payload,
        sys.stdout,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
