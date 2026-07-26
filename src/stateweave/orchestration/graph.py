"""Deterministic orchestration contracts with no runtime dispatch side effects."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable

from stateweave.contracts import require_contract, validate_contract
from stateweave.core.errors import ContractError
from stateweave.core.io import canonical_json_bytes, sha256_bytes

SCHEMA_BY_KIND = {
    "task": "task.schema.json",
    "input_manifest": "input-manifest.schema.json",
    "worker": "worker.schema.json",
    "execution_receipt": "execution-receipt.schema.json",
    "evaluation": "evaluation.schema.json",
}
RISK_RANK = {
    "low": 0,
    "moderate": 1,
    "high": 2,
    "critical": 3,
}


@dataclass
class ExecutionReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    document_count: int = 0
    task_order: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "document_count": self.document_count,
            "task_order": list(self.task_order),
            "errors": sorted(set(self.errors)),
            "warnings": sorted(set(self.warnings)),
        }


def manifest_digest(manifest: dict[str, Any]) -> str:
    """Return the canonical SHA-256 used to bind a receipt to its inputs."""

    return sha256_bytes(canonical_json_bytes(manifest))


def topological_order(tasks: Iterable[dict[str, Any]]) -> list[str]:
    """Return a stable DAG order or raise for missing edges and cycles."""

    by_id: dict[str, dict[str, Any]] = {}
    for task in tasks:
        identifier = task.get("id")
        if not isinstance(identifier, str):
            raise ContractError("task id must be a string")
        if identifier in by_id:
            raise ContractError(f"duplicate task id {identifier}")
        by_id[identifier] = task

    dependencies: dict[str, set[str]] = {}
    dependents: dict[str, set[str]] = {identifier: set() for identifier in by_id}
    for identifier, task in by_id.items():
        raw_dependencies = task.get("dependencies")
        if not isinstance(raw_dependencies, list) or any(
            not isinstance(item, str) for item in raw_dependencies
        ):
            raise ContractError(f"{identifier}: dependencies must be strings")
        missing = sorted(set(raw_dependencies) - set(by_id))
        if missing:
            raise ContractError(f"{identifier}: missing dependencies {missing}")
        dependencies[identifier] = set(raw_dependencies)
        for dependency in raw_dependencies:
            dependents[dependency].add(identifier)

    ready = sorted(
        identifier for identifier, required in dependencies.items() if not required
    )
    ordered: list[str] = []
    while ready:
        current = ready.pop(0)
        ordered.append(current)
        for dependent in sorted(dependents[current]):
            dependencies[dependent].discard(current)
            if not dependencies[dependent] and dependent not in ordered:
                ready.append(dependent)
        ready.sort()
    if len(ordered) != len(by_id):
        cyclic = sorted(set(by_id) - set(ordered))
        raise ContractError(f"execution graph contains a cycle: {cyclic}")
    return ordered


def route_task(
    task: dict[str, Any],
    workers: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    """Select a stable eligible worker without invoking any runtime."""

    require_contract(
        task,
        package="stateweave.orchestration",
        filename="task.schema.json",
        source=task.get("id", "task"),
    )
    required = frozenset(task["required_capabilities"])
    task_risk = RISK_RANK[task["risk"]]
    eligible: list[dict[str, Any]] = []
    for index, worker in enumerate(workers):
        require_contract(
            worker,
            package="stateweave.orchestration",
            filename="worker.schema.json",
            source=worker.get("id", f"workers[{index}]"),
        )
        if task_risk > RISK_RANK[worker["risk_ceiling"]]:
            continue
        if not required.issubset(worker["capabilities"]):
            continue
        eligible.append(worker)
    if not eligible:
        raise ContractError(
            f"{task['id']}: no worker satisfies capabilities and risk ceiling"
        )
    return min(eligible, key=lambda worker: (worker["priority"], worker["id"]))


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def audit_execution(documents: Iterable[dict[str, Any]]) -> ExecutionReport:
    """Validate orchestration schemas and cross-document invariants."""

    materialized = list(documents)
    report = ExecutionReport(document_count=len(materialized))
    by_id: dict[str, dict[str, Any]] = {}
    for index, document in enumerate(materialized):
        kind = document.get("kind")
        filename = SCHEMA_BY_KIND.get(kind) if isinstance(kind, str) else None
        source = f"documents[{index}]"
        if filename is None:
            report.errors.append(f"{source}: unsupported orchestration kind {kind!r}")
        else:
            report.errors.extend(
                validate_contract(
                    document,
                    package="stateweave.orchestration",
                    filename=filename,
                    source=source,
                )
            )
        identifier = document.get("id")
        if isinstance(identifier, str):
            if identifier in by_id:
                report.errors.append(f"duplicate orchestration id {identifier}")
            else:
                by_id[identifier] = document

    tasks = {
        identifier: document
        for identifier, document in by_id.items()
        if document.get("kind") == "task"
    }
    manifests = {
        identifier: document
        for identifier, document in by_id.items()
        if document.get("kind") == "input_manifest"
    }
    workers = {
        identifier: document
        for identifier, document in by_id.items()
        if document.get("kind") == "worker"
    }
    receipts = {
        identifier: document
        for identifier, document in by_id.items()
        if document.get("kind") == "execution_receipt"
    }
    evaluations = {
        identifier: document
        for identifier, document in by_id.items()
        if document.get("kind") == "evaluation"
    }

    try:
        report.task_order = topological_order(tasks.values())
    except ContractError as exc:
        report.errors.append(str(exc))

    for identifier, task in tasks.items():
        manifest_id = task.get("input_manifest_id")
        manifest = manifests.get(manifest_id) if isinstance(manifest_id, str) else None
        if manifest is None:
            report.errors.append(
                f"{identifier}: missing input manifest {manifest_id!r}"
            )
        elif manifest.get("task_id") != identifier:
            report.errors.append(
                f"{identifier}: manifest {manifest_id} belongs to another task"
            )

    for identifier, manifest in manifests.items():
        task_id = manifest.get("task_id")
        if not isinstance(task_id, str) or task_id not in tasks:
            report.errors.append(f"{identifier}: missing task {task_id!r}")

    for identifier, receipt in receipts.items():
        task_id = receipt.get("task_id")
        worker_id = receipt.get("worker_id")
        matched_task = tasks.get(task_id) if isinstance(task_id, str) else None
        if matched_task is None:
            report.errors.append(f"{identifier}: missing task {task_id!r}")
        if not isinstance(worker_id, str) or worker_id not in workers:
            report.errors.append(f"{identifier}: missing worker {worker_id!r}")
        if matched_task is not None:
            manifest_id = matched_task.get("input_manifest_id")
            matched_manifest = (
                manifests.get(manifest_id) if isinstance(manifest_id, str) else None
            )
            if matched_manifest is not None and receipt.get(
                "input_manifest_sha256"
            ) != manifest_digest(matched_manifest):
                report.errors.append(
                    f"{identifier}: input manifest digest does not match {manifest_id}"
                )
        started = _parse_datetime(receipt.get("started_at"))
        finished = _parse_datetime(receipt.get("finished_at"))
        if started is not None and finished is not None and finished < started:
            report.errors.append(f"{identifier}: finished_at precedes started_at")

    for identifier, evaluation in evaluations.items():
        receipt_id = evaluation.get("receipt_id")
        if not isinstance(receipt_id, str) or receipt_id not in receipts:
            report.errors.append(
                f"{identifier}: missing execution receipt {receipt_id!r}"
            )

    report.errors = sorted(set(report.errors))
    report.warnings = sorted(set(report.warnings))
    return report
