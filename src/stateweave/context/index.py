"""Hash-bound, rebuildable cache for context retrieval snapshots."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from stateweave.contracts import require_contract, validate_contract
from stateweave.core.audit import AuditReport, Conflict, LoadedRecord, audit_repository
from stateweave.core.audit import load_records
from stateweave.core.backup import project_writer_lock
from stateweave.core.config import ProjectConfig
from stateweave.core.errors import ContractError, RecordError
from stateweave.core.io import (
    atomic_write_json,
    canonical_json_bytes,
    read_json,
    safe_relative_path,
    sha256_bytes,
    sha256_file,
)
from stateweave.core.layout import inspect_store_layout
from stateweave.core.schema import validate_record
from stateweave.core.transactions import inspect_transaction_store

PACKAGE = "stateweave.context"
MAX_INDEX_BYTES = 128 * 1024 * 1024


def context_index_path(config: ProjectConfig) -> Path:
    return config.extensions_dir / "context" / "index.json"


def _snapshot_digest(revisions: dict[str, str]) -> str:
    snapshot = [
        {"id": identifier, "revision_sha256": revision}
        for identifier, revision in sorted(revisions.items())
    ]
    return sha256_bytes(canonical_json_bytes(snapshot))


def _digest_payload(index: dict[str, Any]) -> dict[str, Any]:
    excluded = {"schema_version", "kind", "id", "index_sha256"}
    return {key: value for key, value in index.items() if key not in excluded}


def _index_digest(index: dict[str, Any]) -> str:
    return sha256_bytes(canonical_json_bytes(_digest_payload(index)))


def build_context_index(config: ProjectConfig, *, as_of: date) -> Path:
    """Rebuild the derived index from one fully audited canonical snapshot."""

    with project_writer_lock(config):
        report = audit_repository(
            config,
            today=as_of,
            allow_active_writer=True,
        )
        fatal = [error for error in report.errors if not error.startswith("conflict:")]
        if fatal:
            raise RecordError(
                "cannot index an invalid memory repository: " + "; ".join(fatal)
            )
        records, errors = load_records(config)
        if errors:
            raise RecordError("cannot index memory records: " + "; ".join(errors))
        revisions = {
            identifier: sha256_file(record.path)
            for identifier, record in records.items()
        }
        entries = [
            {
                "id": identifier,
                "record_kind": record.kind,
                "source_path": record.path.relative_to(config.root).as_posix(),
                "revision_sha256": revisions[identifier],
                "content": record.data,
            }
            for identifier, record in sorted(records.items())
        ]
        payload = {
            "config_sha256": sha256_file(config.source),
            "snapshot_sha256": _snapshot_digest(revisions),
            "audited_as_of": as_of.isoformat(),
            "records": entries,
            "review_queue": sorted(
                report.review_queue,
                key=lambda item: (
                    item.get("due", ""),
                    item.get("id", ""),
                    item.get("reason", ""),
                ),
            ),
            "conflicts": [
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
            ],
        }
        digest = sha256_bytes(canonical_json_bytes(payload))
        index = {
            "schema_version": 1,
            "kind": "context_index",
            "id": f"IDX-{digest}",
            "index_sha256": digest,
            **payload,
        }
        require_contract(
            index,
            package=PACKAGE,
            filename="context-index.schema.json",
            source="context-index",
        )
        path = context_index_path(config)
        if path.parent.is_symlink():
            raise RecordError(f"context index directory may not be a symlink: {path}")
        atomic_write_json(path, index)
        return path


def _verified_index(
    config: ProjectConfig,
    *,
    as_of: date,
) -> tuple[dict[str, LoadedRecord], dict[str, str], AuditReport, str]:
    path = context_index_path(config)
    if path.is_symlink() or not path.is_file():
        raise RecordError("context index is absent or not a real file")
    index = read_json(path, max_bytes=MAX_INDEX_BYTES)
    if not isinstance(index, dict):
        raise RecordError("context index must be an object")
    errors = validate_contract(
        index,
        package=PACKAGE,
        filename="context-index.schema.json",
        source=path,
    )
    if errors:
        raise RecordError("; ".join(errors))
    digest = _index_digest(index)
    if index["index_sha256"] != digest or index["id"] != f"IDX-{digest}":
        raise RecordError("context index digest or id does not match")
    if index["config_sha256"] != sha256_file(config.source):
        raise RecordError("context index configuration has drifted")
    if index["audited_as_of"] != as_of.isoformat():
        raise RecordError("context index was audited for a different date")
    transaction_report = inspect_transaction_store(config)
    if transaction_report.errors:
        raise RecordError("context index blocked by incomplete transaction")

    layout = inspect_store_layout(config)
    if layout.errors:
        raise RecordError("; ".join(layout.errors))
    layout_by_path = {
        path.relative_to(config.root).as_posix(): (kind, path)
        for kind, path in layout.record_paths
    }
    records: dict[str, LoadedRecord] = {}
    revisions: dict[str, str] = {}
    indexed_paths: set[str] = set()
    for entry in index["records"]:
        relative = safe_relative_path(entry["source_path"]).as_posix()
        indexed_paths.add(relative)
        matched = layout_by_path.get(relative)
        if matched is None:
            raise RecordError(f"context index source is missing: {relative}")
        expected_kind, source = matched
        identifier = entry["id"]
        if entry["record_kind"] != expected_kind:
            raise RecordError(f"context index kind drifted: {identifier}")
        if entry["content"].get("id") != identifier:
            raise RecordError(f"context index record id drifted: {identifier}")
        record_errors = validate_record(entry["content"], expected_kind, source)
        if record_errors:
            raise RecordError("; ".join(record_errors))
        revision = sha256_file(source)
        if revision != entry["revision_sha256"]:
            raise RecordError(f"context index source drifted: {identifier}")
        if identifier in records:
            raise RecordError(f"duplicate context index record: {identifier}")
        records[identifier] = LoadedRecord(
            identifier,
            expected_kind,
            source,
            entry["content"],
        )
        revisions[identifier] = revision
    if indexed_paths != set(layout_by_path):
        raise RecordError("context index does not cover the canonical layout")
    if index["snapshot_sha256"] != _snapshot_digest(revisions):
        raise RecordError("context index snapshot digest does not match")

    report = AuditReport(
        review_queue=list(index["review_queue"]),
        conflicts=[
            Conflict(
                item["left_id"],
                item["right_id"],
                item["subject"],
                item["predicate"],
                item["scope"],
            )
            for item in index["conflicts"]
        ],
        record_count=len(records),
    )
    return records, revisions, report, digest


def load_verified_context_index(
    config: ProjectConfig,
    *,
    as_of: date,
) -> tuple[dict[str, LoadedRecord], dict[str, str], AuditReport] | None:
    """Return an exact cached snapshot, or None so callers safely fall back."""

    try:
        records, revisions, report, _ = _verified_index(config, as_of=as_of)
    except (ContractError, RecordError, OSError, KeyError, TypeError, ValueError):
        return None
    return records, revisions, report


def inspect_context_index(config: ProjectConfig, *, as_of: date) -> dict[str, Any]:
    """Explain whether the derived index is usable without rebuilding it."""

    with project_writer_lock(config):
        try:
            records, _, _, digest = _verified_index(config, as_of=as_of)
        except (
            ContractError,
            RecordError,
            OSError,
            KeyError,
            TypeError,
            ValueError,
        ) as exc:
            return {"valid": False, "reason": str(exc), "record_count": 0}
        return {
            "valid": True,
            "reason": "configuration, date, layout, and record hashes match",
            "record_count": len(records),
            "index_sha256": digest,
        }
