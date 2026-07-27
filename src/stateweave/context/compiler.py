"""Explainable retrieval and deterministic context compilation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from stateweave.content import ContentInspector, inspect_content
from stateweave.contracts import require_contract
from stateweave.core.audit import (
    AuditReport,
    LoadedRecord,
    audit_repository,
    load_records,
)
from stateweave.core.backup import project_writer_lock
from stateweave.core.config import ProjectConfig
from stateweave.core.errors import RecordError
from stateweave.core.io import canonical_json_bytes, sha256_bytes, sha256_file
from stateweave.context.index import load_verified_context_index

PACKAGE = "stateweave.context"
TOKEN = re.compile(r"[^\W_]{2,}", re.UNICODE)
CONFIDENCE = {"low": 0, "medium": 1, "high": 2}
STATUS_SCORE = {
    "verified": 30,
    "accepted": 30,
    "provisional": 10,
    "proposed": 10,
    "disputed": -5,
    "deprecated": -20,
    "superseded": -20,
    "rejected": -20,
}


@dataclass(frozen=True)
class _RankedRecord:
    record: LoadedRecord
    revision_sha256: str
    score: int
    reasons: tuple[str, ...]


def _query_digest(query: dict[str, Any]) -> str:
    return sha256_bytes(canonical_json_bytes(query))


def _query_terms(query: dict[str, Any]) -> tuple[str, ...]:
    explicit = [item.casefold() for item in query["terms"]]
    objective = TOKEN.findall(query["objective"].casefold())
    return tuple(sorted(set(explicit + objective)))


def _status(record: LoadedRecord) -> str | None:
    value = record.data.get("status")
    return value if isinstance(value, str) else None


def _matches_filters(record: LoadedRecord, query: dict[str, Any]) -> bool:
    filters = query["filters"]
    kinds = filters["record_kinds"]
    if kinds and record.kind not in kinds:
        return False
    statuses = filters["statuses"]
    if record.kind != "state" and statuses and _status(record) not in statuses:
        return False
    domains = filters["domains"]
    if domains and record.data.get("domain") not in domains:
        return False
    classifications = filters["classifications"]
    if classifications and record.data.get("classification") not in classifications:
        return False
    minimum = filters.get("minimum_confidence")
    if minimum is not None and record.kind == "fact":
        observed = record.data.get("confidence")
        if not isinstance(observed, str):
            return False
        if CONFIDENCE.get(observed, -1) < CONFIDENCE[minimum]:
            return False
    return True


def _search_fields(record: LoadedRecord) -> dict[str, str]:
    data = record.data
    title = str(data.get("title", ""))
    metadata = " ".join(
        str(data.get(name, ""))
        for name in ("id", "domain", "fact_class", "status", "confidence")
    )
    if record.kind == "fact":
        body_values = [data.get("statement", ""), data.get("claim", {})]
    elif record.kind == "decision":
        body_values = [
            data.get("context", ""),
            data.get("decision", ""),
            data.get("consequences", []),
        ]
    else:
        body_values = [data.get("items", [])]
    return {
        "title": title.casefold(),
        "metadata": metadata.casefold(),
        "body": " ".join(str(value) for value in body_values).casefold(),
    }


def _direct_rank(
    record: LoadedRecord,
    terms: tuple[str, ...],
    objective: str,
) -> tuple[int, tuple[str, ...]] | None:
    fields = _search_fields(record)
    reasons: list[str] = []
    status = _status(record)
    score = 25 if record.kind == "state" else 0
    if status is not None:
        score = STATUS_SCORE.get(status, score)
    objective_folded = objective.casefold().strip()
    combined = " ".join(fields.values())
    if objective_folded in combined:
        score += 24
        reasons.append("objective_phrase")
    for term in terms:
        matched: list[str] = []
        if term in fields["title"]:
            score += 12
            matched.append("title")
        if term in fields["metadata"]:
            score += 8
            matched.append("metadata")
        if term in fields["body"]:
            score += 4
            matched.append("body")
        if matched:
            reasons.append(f"term:{term}:{'+'.join(matched)}")
    if not reasons:
        return None
    return score, tuple(sorted(set(reasons)))


def _relations(records: dict[str, LoadedRecord]) -> dict[str, set[str]]:
    adjacency: dict[str, set[str]] = {identifier: set() for identifier in records}
    for identifier, record in records.items():
        targets: list[Any] = list(record.data.get("references", []))
        targets.extend(record.data.get("supersedes", []))
        superseded_by = record.data.get("superseded_by")
        if superseded_by is not None:
            targets.append(superseded_by)
        if record.kind == "state":
            targets.extend(
                item.get("source_id")
                for item in record.data.get("items", [])
                if isinstance(item, dict)
            )
        for target in targets:
            if isinstance(target, str) and target in records:
                adjacency[identifier].add(target)
                adjacency[target].add(identifier)
    return adjacency


def _rank_records(
    records: dict[str, LoadedRecord],
    revisions: dict[str, str],
    query: dict[str, Any],
) -> tuple[list[_RankedRecord], dict[str, int]]:
    terms = _query_terms(query)
    eligible = {
        identifier: record
        for identifier, record in records.items()
        if _matches_filters(record, query)
    }
    direct: dict[str, tuple[int, tuple[str, ...]]] = {}
    for identifier, record in eligible.items():
        ranked = _direct_rank(record, terms, query["objective"])
        if ranked is not None:
            direct[identifier] = ranked

    expanded = dict(direct)
    adjacency = _relations(records)
    frontier = sorted(direct)
    for depth in range(1, query["relation_depth"] + 1):
        next_frontier: list[str] = []
        for source in frontier:
            source_score = expanded[source][0]
            for target in sorted(adjacency[source]):
                if target not in eligible or target in expanded:
                    continue
                expanded[target] = (
                    max(1, source_score - 20),
                    (f"related:{source}:depth={depth}",),
                )
                next_frontier.append(target)
        frontier = sorted(set(next_frontier))
        if not frontier:
            break

    ranked_records = [
        _RankedRecord(records[identifier], revisions[identifier], score, reasons)
        for identifier, (score, reasons) in expanded.items()
    ]
    ranked_records.sort(key=lambda item: (-item.score, item.record.identifier))
    return ranked_records, {
        "filtered": len(records) - len(eligible),
        "no_match": len(eligible) - len(expanded),
        "item_limit": 0,
        "content_budget": 0,
        "content_policy": 0,
    }


def _snapshot_digest(revisions: dict[str, str]) -> str:
    snapshot = [
        {"id": identifier, "revision_sha256": revision}
        for identifier, revision in sorted(revisions.items())
    ]
    return sha256_bytes(canonical_json_bytes(snapshot))


def _match_payload(config: ProjectConfig, item: _RankedRecord) -> dict[str, Any]:
    record = item.record
    return {
        "id": record.identifier,
        "record_kind": record.kind,
        "status": _status(record),
        "classification": record.data["classification"],
        "revision_sha256": item.revision_sha256,
        "source_path": record.path.relative_to(config.root).as_posix(),
        "score": item.score,
        "reasons": list(item.reasons),
    }


def _conflicts(report: AuditReport) -> list[dict[str, str]]:
    return [
        {
            "left_id": item.left_id,
            "right_id": item.right_id,
            "subject": item.subject,
            "predicate": item.predicate,
            "scope": item.scope,
        }
        for item in sorted(
            report.conflicts,
            key=lambda conflict: (conflict.left_id, conflict.right_id),
        )
    ]


def _warnings(
    selected: list[_RankedRecord],
    report: AuditReport,
) -> list[dict[str, str]]:
    selected_ids = {item.record.identifier for item in selected}
    messages: set[tuple[str, str, str]] = set()
    for item in selected:
        identifier = item.record.identifier
        status = _status(item.record)
        if status in {"disputed", "deprecated", "superseded", "rejected"}:
            messages.add(
                (
                    f"status_{status}",
                    identifier,
                    f"{identifier} has governed status {status}",
                )
            )
    for review in report.review_queue:
        review_identifier = review.get("id")
        reason = review.get("reason")
        due = review.get("due")
        if (
            isinstance(review_identifier, str)
            and review_identifier in selected_ids
            and isinstance(reason, str)
        ):
            messages.add(
                (
                    f"review_{reason}",
                    review_identifier,
                    f"{review_identifier} requires {reason} review as of {due}",
                )
            )
    return [
        {"code": code, "record_id": identifier, "message": message}
        for code, identifier, message in sorted(messages)
    ]


def _load_snapshot(
    config: ProjectConfig,
    query: dict[str, Any],
) -> tuple[dict[str, LoadedRecord], dict[str, str], AuditReport]:
    as_of = date.fromisoformat(query["as_of"])
    indexed = load_verified_context_index(config, as_of=as_of)
    if indexed is not None:
        return indexed
    report = audit_repository(config, today=as_of, allow_active_writer=True)
    fatal_errors = [
        error for error in report.errors if not error.startswith("conflict:")
    ]
    if fatal_errors:
        raise RecordError(
            "memory query requires a valid repository: " + "; ".join(fatal_errors)
        )
    records, load_errors = load_records(config)
    if load_errors:
        raise RecordError("memory snapshot is invalid: " + "; ".join(load_errors))
    revisions = {
        identifier: sha256_file(record.path) for identifier, record in records.items()
    }
    return records, revisions, report


def _verify_snapshot(
    records: dict[str, LoadedRecord],
    revisions: dict[str, str],
) -> None:
    for identifier, record in records.items():
        if sha256_file(record.path) != revisions[identifier]:
            raise RecordError(f"memory snapshot drifted while reading: {identifier}")


def query_memory(config: ProjectConfig, query: dict[str, Any]) -> dict[str, Any]:
    """Return deterministic, explainable matches without embedding record content."""

    require_contract(
        query,
        package=PACKAGE,
        filename="memory-query.schema.json",
        source="memory-query",
    )
    with project_writer_lock(config):
        records, revisions, report = _load_snapshot(config, query)
        ranked, excluded = _rank_records(records, revisions, query)
        max_items = query["budget"]["max_items"]
        selected = ranked[:max_items]
        excluded["item_limit"] = max(0, len(ranked) - len(selected))
        result = {
            "schema_version": 1,
            "kind": "memory_query_result",
            "query_sha256": _query_digest(query),
            "snapshot_sha256": _snapshot_digest(revisions),
            "as_of": query["as_of"],
            "matches": [_match_payload(config, item) for item in selected],
            "warnings": _warnings(selected, report),
            "conflicts": _conflicts(report),
            "excluded": excluded,
        }
        require_contract(
            result,
            package=PACKAGE,
            filename="memory-query-result.schema.json",
            source="memory-query-result",
        )
        _verify_snapshot(records, revisions)
        return result


def compile_context(
    config: ProjectConfig,
    query: dict[str, Any],
    *,
    content_inspector: ContentInspector | None = None,
) -> dict[str, Any]:
    """Compile a hash-bound ContextBundle within the configured content budget."""

    require_contract(
        query,
        package=PACKAGE,
        filename="memory-query.schema.json",
        source="memory-query",
    )
    with project_writer_lock(config):
        records, revisions, report = _load_snapshot(config, query)
        ranked, excluded = _rank_records(records, revisions, query)
        max_items = query["budget"]["max_items"]
        max_bytes = query["budget"]["max_content_bytes"]
        selected: list[_RankedRecord] = []
        items: list[dict[str, Any]] = []
        content_warnings: list[dict[str, str]] = []
        content_bytes = 0
        for ranked_item in ranked:
            if len(items) >= max_items:
                excluded["item_limit"] += 1
                continue
            findings = inspect_content(
                ranked_item.record.data,
                phase="retrieval",
                inspector=content_inspector,
            )
            if any(finding.severity == "block" for finding in findings):
                excluded["content_policy"] += 1
                continue
            content_warnings.extend(
                {
                    "code": finding.code,
                    "record_id": ranked_item.record.identifier,
                    "message": f"{finding.message} at {finding.path}",
                }
                for finding in findings
            )
            item = _match_payload(config, ranked_item)
            item["content"] = ranked_item.record.data
            item_bytes = len(canonical_json_bytes(item))
            if content_bytes + item_bytes > max_bytes:
                excluded["content_budget"] += 1
                continue
            items.append(item)
            selected.append(ranked_item)
            content_bytes += item_bytes

        payload = {
            "query_sha256": _query_digest(query),
            "snapshot_sha256": _snapshot_digest(revisions),
            "as_of": query["as_of"],
            "objective": query["objective"],
            "trust": {
                "treat_content_as_untrusted": True,
                "authority": "evidence_only",
            },
            "budget": dict(query["budget"]),
            "usage": {
                "selected_items": len(items),
                "content_bytes": content_bytes,
                "estimated_tokens": (content_bytes + 3) // 4,
            },
            "items": items,
            "warnings": sorted(
                _warnings(selected, report) + content_warnings,
                key=lambda item: (
                    item["record_id"],
                    item["code"],
                    item["message"],
                ),
            ),
            "conflicts": _conflicts(report),
            "excluded": excluded,
        }
        context_sha256 = sha256_bytes(canonical_json_bytes(payload))
        bundle = {
            "schema_version": 1,
            "kind": "context_bundle",
            "id": f"CTX-{context_sha256}",
            "context_sha256": context_sha256,
            **payload,
        }
        require_contract(
            bundle,
            package=PACKAGE,
            filename="context-bundle.schema.json",
            source="context-bundle",
        )
        _verify_snapshot(records, revisions)
        return bundle
