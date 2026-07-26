"""Schema-backed semantic audit for governed workflow records."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from stateweave.contracts import validate_contract

SCHEMA_BY_KIND = {
    "work_request": "work-request.schema.json",
    "handoff": "handoff.schema.json",
    "acceptance": "acceptance.schema.json",
}


@dataclass
class WorkflowReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    record_count: int = 0

    @property
    def ok(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "record_count": self.record_count,
            "errors": sorted(set(self.errors)),
            "warnings": sorted(set(self.warnings)),
        }


def _role_fields(record: dict[str, Any]) -> Iterable[tuple[str, Any]]:
    for field_name in (
        "requester_role",
        "assignee_role",
        "producer_role",
        "receiver_role",
        "decider_role",
    ):
        if field_name in record:
            yield field_name, record[field_name]


def audit_workflow(
    records: Iterable[dict[str, Any]],
    *,
    roles: Iterable[str],
) -> WorkflowReport:
    """Validate workflow documents and their cross-record lifecycle."""

    role_set = frozenset(roles)
    materialized = list(records)
    report = WorkflowReport(record_count=len(materialized))
    by_id: dict[str, dict[str, Any]] = {}

    for index, record in enumerate(materialized):
        kind = record.get("kind")
        filename = SCHEMA_BY_KIND.get(kind) if isinstance(kind, str) else None
        source = f"records[{index}]"
        if filename is None:
            report.errors.append(f"{source}: unsupported workflow kind {kind!r}")
        else:
            report.errors.extend(
                validate_contract(
                    record,
                    package="stateweave.workflow",
                    filename=filename,
                    source=source,
                )
            )
        identifier = record.get("id")
        if isinstance(identifier, str):
            if identifier in by_id:
                report.errors.append(f"duplicate workflow id {identifier}")
            else:
                by_id[identifier] = record
        for field_name, role in _role_fields(record):
            if not isinstance(role, str) or role not in role_set:
                report.errors.append(
                    f"{identifier or source}: {field_name} {role!r} is not configured"
                )

    requests = {
        identifier: record
        for identifier, record in by_id.items()
        if record.get("kind") == "work_request"
    }
    handoffs = {
        identifier: record
        for identifier, record in by_id.items()
        if record.get("kind") == "handoff"
    }
    acceptances = {
        identifier: record
        for identifier, record in by_id.items()
        if record.get("kind") == "acceptance"
    }

    handoffs_by_request: dict[str, list[dict[str, Any]]] = {}
    for identifier, handoff in handoffs.items():
        request_id = handoff.get("work_request_id")
        if not isinstance(request_id, str) or request_id not in requests:
            report.errors.append(f"{identifier}: missing work request {request_id!r}")
            continue
        handoffs_by_request.setdefault(request_id, []).append(handoff)

    acceptance_by_handoff: dict[str, dict[str, Any]] = {}
    for identifier, acceptance in acceptances.items():
        request_id = acceptance.get("work_request_id")
        handoff_id = acceptance.get("handoff_id")
        matched_handoff = (
            handoffs.get(handoff_id) if isinstance(handoff_id, str) else None
        )
        if not isinstance(request_id, str) or request_id not in requests:
            report.errors.append(f"{identifier}: missing work request {request_id!r}")
        if matched_handoff is None:
            report.errors.append(f"{identifier}: missing handoff {handoff_id!r}")
            continue
        if matched_handoff.get("work_request_id") != request_id:
            report.errors.append(
                f"{identifier}: handoff {handoff_id} belongs to a different request"
            )
        assert isinstance(handoff_id, str)
        if handoff_id in acceptance_by_handoff:
            report.errors.append(
                f"{identifier}: handoff {handoff_id} has multiple acceptances"
            )
        else:
            acceptance_by_handoff[handoff_id] = acceptance

    for identifier, request in requests.items():
        status = request.get("status")
        request_handoffs = handoffs_by_request.get(identifier, [])
        if (
            isinstance(status, str)
            and status in {"ready", "accepted"}
            and not request_handoffs
        ):
            report.errors.append(f"{identifier}: status {status!r} requires a handoff")
        accepted: list[dict[str, Any]] = []
        for handoff in request_handoffs:
            handoff_id = handoff.get("id")
            if not isinstance(handoff_id, str):
                continue
            matched_acceptance = acceptance_by_handoff.get(handoff_id)
            if matched_acceptance and matched_acceptance.get("outcome") == "accepted":
                accepted.append(matched_acceptance)
        if status == "accepted" and not accepted:
            report.errors.append(
                f"{identifier}: accepted status requires an accepted handoff"
            )
        if status != "accepted" and accepted:
            report.errors.append(
                f"{identifier}: accepted handoff requires request status 'accepted'"
            )

    report.errors = sorted(set(report.errors))
    report.warnings = sorted(set(report.warnings))
    return report
