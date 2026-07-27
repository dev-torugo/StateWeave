"""Cross-record semantic audit for persistent memory."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from stateweave.core.config import ProjectConfig
from stateweave.core.errors import RecordError
from stateweave.core.io import canonical_json_bytes, read_json
from stateweave.core.layout import inspect_store_layout
from stateweave.core.schema import validate_record
from stateweave.core.transactions import inspect_transaction_store

RECORD_ID = re.compile(r"^(?:FCT|DEC|STATE)-[A-Za-z0-9][A-Za-z0-9._-]{1,127}$")
SchemaValidator = Callable[[dict[str, Any], str, Path], list[str]]


@dataclass(frozen=True)
class LoadedRecord:
    identifier: str
    kind: str
    path: Path
    data: dict[str, Any]


@dataclass(frozen=True)
class Backlink:
    source_id: str
    relation: str


@dataclass(frozen=True)
class Conflict:
    left_id: str
    right_id: str
    subject: str
    predicate: str
    scope: str


@dataclass
class AuditReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    review_queue: list[dict[str, str]] = field(default_factory=list)
    backlinks: dict[str, list[Backlink]] = field(default_factory=dict)
    conflicts: list[Conflict] = field(default_factory=list)
    record_count: int = 0

    @property
    def ok(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "record_count": self.record_count,
            "errors": sorted(self.errors),
            "warnings": sorted(self.warnings),
            "review_queue": sorted(
                self.review_queue,
                key=lambda item: (
                    item.get("due", ""),
                    item.get("id", ""),
                    item.get("reason", ""),
                ),
            ),
            "backlinks": {
                identifier: [
                    {"source_id": item.source_id, "relation": item.relation}
                    for item in sorted(
                        links,
                        key=lambda item: (item.source_id, item.relation),
                    )
                ]
                for identifier, links in sorted(self.backlinks.items())
            },
            "conflicts": [
                {
                    "left_id": item.left_id,
                    "right_id": item.right_id,
                    "subject": item.subject,
                    "predicate": item.predicate,
                    "scope": item.scope,
                }
                for item in sorted(
                    self.conflicts,
                    key=lambda item: (item.left_id, item.right_id),
                )
            ],
        }


def load_records(
    config: ProjectConfig,
    *,
    schema_validator: SchemaValidator | None = None,
) -> tuple[dict[str, LoadedRecord], list[str]]:
    records: dict[str, LoadedRecord] = {}
    layout = inspect_store_layout(config)
    errors = list(layout.errors)
    paths = list(layout.record_paths)
    if len(paths) > config.limits.max_records:
        errors.append(
            f"record count {len(paths)} exceeds configured limit "
            f"{config.limits.max_records}"
        )
        return {}, errors
    for expected_kind, path in paths:
        if path.is_symlink():
            errors.append(
                f"{path.relative_to(config.root)}: record may not be a symlink"
            )
            continue
        try:
            payload = read_json(path, max_bytes=config.limits.max_record_bytes)
        except RecordError as exc:
            errors.append(str(exc))
            continue
        relative = str(path.relative_to(config.root))
        if not isinstance(payload, dict):
            errors.append(f"{relative}: record must be an object")
            continue
        errors.extend(validate_record(payload, expected_kind, path))
        if schema_validator is not None:
            errors.extend(schema_validator(payload, expected_kind, path))
        identifier = payload.get("id")
        kind = payload.get("kind")
        if not isinstance(identifier, str) or RECORD_ID.fullmatch(identifier) is None:
            errors.append(f"{relative}: invalid or missing record id")
            continue
        if kind != expected_kind:
            errors.append(
                f"{relative}: kind {kind!r} does not match directory kind "
                f"{expected_kind!r}"
            )
            continue
        if identifier in records:
            errors.append(
                f"{relative}: duplicate id {identifier}; first seen in "
                f"{records[identifier].path.relative_to(config.root)}"
            )
            continue
        if expected_kind != "state" and path.stem != identifier:
            errors.append(f"{relative}: filename must be {identifier}.json")
        records[identifier] = LoadedRecord(identifier, kind, path, payload)
    return records, errors


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _parse_date(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _add_backlink(
    backlinks: dict[str, list[Backlink]],
    target: str,
    source: str,
    relation: str,
) -> None:
    backlinks.setdefault(target, []).append(Backlink(source, relation))


def _supersession_cycle(records: dict[str, LoadedRecord]) -> list[str] | None:
    adjacency = {
        identifier: [
            target
            for target in _string_list(record.data.get("supersedes"))
            if target in records
        ]
        for identifier, record in records.items()
    }
    color: dict[str, int] = {}
    for start in sorted(records):
        if color.get(start) == 2:
            continue
        active_path: list[str] = []
        stack: list[tuple[str, bool]] = [(start, False)]
        while stack:
            identifier, expanded = stack.pop()
            if expanded:
                if active_path and active_path[-1] == identifier:
                    active_path.pop()
                color[identifier] = 2
                continue
            state = color.get(identifier, 0)
            if state == 2:
                continue
            if state == 1:
                if identifier in active_path:
                    index = active_path.index(identifier)
                    return active_path[index:] + [identifier]
                continue
            color[identifier] = 1
            active_path.append(identifier)
            stack.append((identifier, True))
            for target in reversed(sorted(adjacency[identifier])):
                if color.get(target) == 1:
                    index = active_path.index(target)
                    return active_path[index:] + [target]
                if color.get(target, 0) == 0:
                    stack.append((target, False))
    return None


def _transitively_supersedes(
    source: str,
    target: str,
    records: dict[str, LoadedRecord],
) -> bool:
    pending = [source]
    seen: set[str] = set()
    while pending:
        current = pending.pop()
        if current in seen or current not in records:
            continue
        seen.add(current)
        for item in _string_list(records[current].data.get("supersedes")):
            if item == target:
                return True
            pending.append(item)
    return False


def _audit_fact_ttl(
    record: LoadedRecord,
    config: ProjectConfig,
    today: date,
    report: AuditReport,
) -> None:
    data = record.data
    status = data.get("status")
    fact_class = data.get("fact_class", config.default_fact_class)
    if not isinstance(fact_class, str):
        report.errors.append(f"{record.identifier}: fact_class must be a string")
        return
    if fact_class not in config.ttl_days and fact_class not in config.no_expiry_classes:
        report.errors.append(f"{record.identifier}: unknown TTL class {fact_class!r}")
    if status in {"provisional", "disputed"}:
        report.review_queue.append(
            {
                "id": record.identifier,
                "reason": str(status),
                "due": today.isoformat(),
            }
        )
        return
    if status != "verified":
        return

    explicit_due = _parse_date(data.get("review_after"))
    verified_at = _parse_datetime(data.get("verified_at"))
    if verified_at is None:
        report.errors.append(
            f"{record.identifier}: verified fact requires valid verified_at"
        )
        return

    if fact_class in config.no_expiry_classes:
        if explicit_due is not None:
            report.warnings.append(
                f"{record.identifier}: no-expiry class has explicit review_after"
            )
        return
    ttl = config.ttl_days.get(fact_class)
    if ttl is None:
        return
    maximum_due = verified_at.date() + timedelta(days=ttl)
    if data.get("review_after") is not None and explicit_due is None:
        report.errors.append(f"{record.identifier}: invalid review_after")
        return
    effective_due = explicit_due or maximum_due
    if (
        config.policy.enforce_ttl_ceiling
        and explicit_due is not None
        and explicit_due > maximum_due
    ):
        report.errors.append(
            f"{record.identifier}: review_after {explicit_due.isoformat()} "
            f"exceeds configured {ttl}-day TTL ({maximum_due.isoformat()})"
        )
    if effective_due < today:
        message = (
            f"{record.identifier}: stale verified fact "
            f"(review due {effective_due.isoformat()})"
        )
        if config.policy.fail_on_stale_verified:
            report.errors.append(message)
        else:
            report.warnings.append(message)
        report.review_queue.append(
            {
                "id": record.identifier,
                "reason": "stale",
                "due": effective_due.isoformat(),
            }
        )
    elif effective_due <= today + timedelta(days=config.policy.review_warning_days):
        report.review_queue.append(
            {
                "id": record.identifier,
                "reason": "due_soon",
                "due": effective_due.isoformat(),
            }
        )


def _audit_conflicts(
    records: dict[str, LoadedRecord],
    report: AuditReport,
) -> None:
    groups: dict[tuple[str, str, str], list[LoadedRecord]] = {}
    for record in records.values():
        data = record.data
        if (
            record.kind != "fact"
            or data.get("status") != "verified"
            or data.get("superseded_by") is not None
        ):
            continue
        claim = data.get("claim")
        if not isinstance(claim, dict):
            continue
        subject = claim.get("subject")
        predicate = claim.get("predicate")
        scope = claim.get("scope", "global")
        if (
            not isinstance(subject, str)
            or not subject
            or not isinstance(predicate, str)
            or not predicate
            or not isinstance(scope, str)
            or not scope
        ):
            continue
        groups.setdefault((subject, predicate, scope), []).append(record)

    for (subject, predicate, scope), group in sorted(groups.items()):
        value_groups: dict[bytes, list[LoadedRecord]] = {}
        for record in sorted(group, key=lambda item: item.identifier):
            value = canonical_json_bytes(record.data["claim"].get("object"))
            value_groups.setdefault(value, []).append(record)
        ordered_groups = [value_groups[key] for key in sorted(value_groups)]
        if len(ordered_groups) < 2:
            continue
        base = ordered_groups[0][0]
        conflicting_ids = [
            item.identifier for items in ordered_groups for item in items
        ]
        for items in ordered_groups[1:]:
            for right in items:
                if _transitively_supersedes(
                    base.identifier, right.identifier, records
                ) or _transitively_supersedes(
                    right.identifier, base.identifier, records
                ):
                    continue
                report.conflicts.append(
                    Conflict(
                        base.identifier,
                        right.identifier,
                        subject,
                        predicate,
                        scope,
                    )
                )
        if report.conflicts and any(
            item.subject == subject
            and item.predicate == predicate
            and item.scope == scope
            for item in report.conflicts
        ):
            report.errors.append(
                f"conflict: {', '.join(conflicting_ids)} assert different "
                f"values for {subject}/{predicate}/{scope}"
            )


def audit_repository(
    config: ProjectConfig,
    *,
    today: date | None = None,
    schema_validator: SchemaValidator | None = None,
    allow_active_writer: bool = False,
    active_transaction_id: str | None = None,
) -> AuditReport:
    """Audit official schemas plus all cross-record invariants."""

    current = today or date.today()
    if not allow_active_writer and (config.metadata_dir / "writer.lock").exists():
        return AuditReport(
            errors=["memory audit blocked while a writer lock is active"],
            record_count=0,
        )
    records, load_errors = load_records(
        config,
        schema_validator=schema_validator,
    )
    report = AuditReport(errors=load_errors, record_count=len(records))
    transaction_report = inspect_transaction_store(
        config,
        active_transaction_id=active_transaction_id,
    )
    report.errors.extend(transaction_report.errors)

    for record in records.values():
        data = record.data
        classification = data.get("classification")
        if classification not in config.policy.allowed_classifications:
            report.errors.append(
                f"{record.identifier}: classification {classification!r} "
                "is not allowed by project policy"
            )
        role_fields = (
            ("owner_role", data.get("owner_role")),
            ("decider_role", data.get("decider_role")),
        )
        for field_name, role in role_fields:
            if role is not None and role not in config.roles:
                report.errors.append(
                    f"{record.identifier}: {field_name} {role!r} is not configured"
                )

        references = _string_list(data.get("references"))
        for target in references:
            if target not in records:
                report.errors.append(f"{record.identifier}: missing reference {target}")
            else:
                _add_backlink(
                    report.backlinks,
                    target,
                    record.identifier,
                    "references",
                )

        if record.kind in {"fact", "decision"}:
            supersedes = _string_list(data.get("supersedes"))
            superseded_by = data.get("superseded_by")
            if superseded_by is not None:
                expected_status = (
                    "deprecated" if record.kind == "fact" else "superseded"
                )
                if data.get("status") != expected_status:
                    report.errors.append(
                        f"{record.identifier}: superseded record status must be "
                        f"{expected_status!r}"
                    )
            for target in supersedes:
                target_record = records.get(target)
                if target_record is None:
                    report.errors.append(
                        f"{record.identifier}: supersedes missing record {target}"
                    )
                    continue
                if target_record.kind != record.kind:
                    report.errors.append(
                        f"{record.identifier}: cannot supersede "
                        f"{target_record.kind} {target}"
                    )
                    continue
                _add_backlink(
                    report.backlinks,
                    target,
                    record.identifier,
                    "supersedes",
                )
                if (
                    config.policy.require_reciprocal_supersession
                    and target_record.data.get("superseded_by") != record.identifier
                ):
                    report.errors.append(
                        f"{record.identifier}: supersedes {target}, but "
                        f"{target}.superseded_by is not reciprocal"
                    )
            if superseded_by is not None:
                target_record = records.get(superseded_by)
                if target_record is None:
                    report.errors.append(
                        f"{record.identifier}: superseded_by missing record "
                        f"{superseded_by}"
                    )
                elif target_record.kind != record.kind:
                    report.errors.append(
                        f"{record.identifier}: superseded_by crosses record kinds"
                    )
                else:
                    _add_backlink(
                        report.backlinks,
                        superseded_by,
                        record.identifier,
                        "superseded_by",
                    )
                    if (
                        config.policy.require_reciprocal_supersession
                        and record.identifier
                        not in _string_list(target_record.data.get("supersedes"))
                    ):
                        report.errors.append(
                            f"{record.identifier}: superseded_by {superseded_by}, "
                            "but reverse supersedes edge is missing"
                        )

        if record.kind == "fact":
            _audit_fact_ttl(record, config, current, report)
        elif record.kind == "state":
            items = data.get("items")
            if isinstance(items, list):
                if len(items) > config.limits.max_state_items:
                    report.errors.append(
                        f"{record.identifier}: state contains {len(items)} items; "
                        f"limit is {config.limits.max_state_items}"
                    )
                for index, item in enumerate(items):
                    if not isinstance(item, dict):
                        continue
                    state_target = item.get("source_id")
                    if isinstance(state_target, str):
                        if state_target not in records:
                            report.errors.append(
                                f"{record.identifier}: items[{index}] references "
                                f"missing source {state_target}"
                            )
                        else:
                            _add_backlink(
                                report.backlinks,
                                state_target,
                                record.identifier,
                                "state_item",
                            )

    cycle = _supersession_cycle(records)
    if cycle:
        report.errors.append(f"supersession cycle: {' -> '.join(cycle)}")
    if config.policy.detect_structured_conflicts:
        _audit_conflicts(records, report)

    report.errors = sorted(set(report.errors))
    report.warnings = sorted(set(report.warnings))
    return report
