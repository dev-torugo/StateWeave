"""Persistent, checkpointed ingestion of untrusted capture requests."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from stateweave.content import ContentInspector, inspect_content
from stateweave.continuity.store import (
    _persist_candidate_locked,
    _prepare_candidate,
)
from stateweave.contracts import require_contract, validate_contract
from stateweave.core.backup import project_writer_lock
from stateweave.core.config import ProjectConfig
from stateweave.core.errors import ContractError, RecordError
from stateweave.core.io import (
    atomic_write_json,
    canonical_json_bytes,
    read_json,
    sha256_bytes,
)

PACKAGE = "stateweave.capture"
MAX_CAPTURE_BYTES = 16 * 1024 * 1024
ENVELOPE_NAME = re.compile(r"^CAP-[a-f0-9]{64}\.json$")
CHECKPOINT_NAME = re.compile(r"^CPT-[a-f0-9]{64}\.json$")


@dataclass
class CaptureReport:
    """Deterministic audit result for capture envelopes and checkpoints."""

    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    envelope_count: int = 0
    checkpoint_count: int = 0
    candidate_binding_count: int = 0

    @property
    def ok(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "errors": sorted(set(self.errors)),
            "warnings": sorted(set(self.warnings)),
            "envelope_count": self.envelope_count,
            "checkpoint_count": self.checkpoint_count,
            "candidate_binding_count": self.candidate_binding_count,
        }


def _root(config: ProjectConfig) -> Path:
    return config.extensions_dir / "capture"


def _envelope_dir(config: ProjectConfig) -> Path:
    return _root(config) / "envelopes"


def _checkpoint_dir(config: ProjectConfig) -> Path:
    return _root(config) / "checkpoints"


def _ensure_store(config: ProjectConfig) -> None:
    for directory in (_envelope_dir(config), _checkpoint_dir(config)):
        if directory.is_symlink():
            raise RecordError(f"capture path may not be a symlink: {directory}")
        directory.mkdir(parents=True, exist_ok=True)


def _read_object(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise RecordError(f"capture artifact must be a real file: {path}")
    payload = read_json(path, max_bytes=MAX_CAPTURE_BYTES)
    if not isinstance(payload, dict):
        raise RecordError(f"capture artifact must be an object: {path}")
    return payload


def _source_sha256(source: dict[str, Any]) -> str:
    return sha256_bytes(canonical_json_bytes(source))


def _checkpoint_id(source: dict[str, Any]) -> str:
    return f"CPT-{_source_sha256(source)}"


def _validate_request(request: dict[str, Any], source: str | Path) -> list[str]:
    errors = validate_contract(
        request,
        package=PACKAGE,
        filename="capture-request.schema.json",
        source=source,
    )
    events = request.get("events")
    if isinstance(events, list):
        identifiers = [
            event.get("event_id") for event in events if isinstance(event, dict)
        ]
        if len(identifiers) != len(set(identifiers)):
            errors.append(f"{source}: capture event ids must be unique")
    cursor = request.get("cursor")
    if (
        isinstance(cursor, dict)
        and cursor.get("before") is not None
        and cursor.get("before") == cursor.get("after")
    ):
        errors.append(f"{source}: capture cursor must advance")
    return sorted(set(errors))


def _envelope_payload(envelope: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in envelope.items() if key != "envelope_sha256"}


def _validate_envelope(envelope: dict[str, Any], source: str | Path) -> list[str]:
    errors = validate_contract(
        envelope,
        package=PACKAGE,
        filename="capture-envelope.schema.json",
        source=source,
    )
    request = envelope.get("request")
    if not isinstance(request, dict):
        return errors
    errors.extend(_validate_request(request, f"{source}:request"))
    request_sha256 = sha256_bytes(canonical_json_bytes(request))
    if envelope.get("request_sha256") != request_sha256:
        errors.append(f"{source}: capture request digest does not match")
    if envelope.get("id") != f"CAP-{request_sha256}":
        errors.append(f"{source}: capture envelope id does not match request")
    envelope_sha256 = sha256_bytes(canonical_json_bytes(_envelope_payload(envelope)))
    if envelope.get("envelope_sha256") != envelope_sha256:
        errors.append(f"{source}: capture envelope digest does not match")
    return sorted(set(errors))


def _checkpoint_payload(checkpoint: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value for key, value in checkpoint.items() if key != "checkpoint_sha256"
    }


def _validate_checkpoint(
    checkpoint: dict[str, Any],
    source: str | Path,
) -> list[str]:
    errors = validate_contract(
        checkpoint,
        package=PACKAGE,
        filename="capture-checkpoint.schema.json",
        source=source,
    )
    source_payload = checkpoint.get("source")
    if not isinstance(source_payload, dict):
        return errors
    source_sha256 = _source_sha256(source_payload)
    if checkpoint.get("source_sha256") != source_sha256:
        errors.append(f"{source}: checkpoint source digest does not match")
    if checkpoint.get("id") != f"CPT-{source_sha256}":
        errors.append(f"{source}: checkpoint id does not match source")
    checkpoint_sha256 = sha256_bytes(
        canonical_json_bytes(_checkpoint_payload(checkpoint))
    )
    if checkpoint.get("checkpoint_sha256") != checkpoint_sha256:
        errors.append(f"{source}: checkpoint digest does not match")
    return sorted(set(errors))


def _checkpoint_for(
    config: ProjectConfig,
    source: dict[str, Any],
) -> tuple[Path, dict[str, Any] | None]:
    path = _checkpoint_dir(config) / f"{_checkpoint_id(source)}.json"
    if not path.exists():
        return path, None
    checkpoint = _read_object(path)
    errors = _validate_checkpoint(checkpoint, path)
    if errors:
        raise RecordError("; ".join(errors))
    return path, checkpoint


def _candidate_provenance(
    event: dict[str, Any],
    envelope_id: str,
) -> dict[str, Any]:
    provenance = dict(event["provenance"])
    derivation_ids = set(provenance["derivation_ids"])
    derivation_ids.add(envelope_id)
    provenance["derivation_ids"] = sorted(derivation_ids)
    return provenance


def _prepare_candidates(
    config: ProjectConfig,
    request: dict[str, Any],
    envelope_id: str,
    *,
    content_inspector: ContentInspector | None,
) -> list[tuple[str, dict[str, Any]]]:
    prepared: list[tuple[str, dict[str, Any]]] = []
    for event in request["events"]:
        candidate = _prepare_candidate(
            config,
            idempotency_key=f"{envelope_id}:{event['event_id']}",
            captured_at=request["captured_at"],
            classification=event["classification"],
            confidence=event["confidence"],
            source=event["source"],
            provenance=_candidate_provenance(event, envelope_id),
            proposed_record=event["proposed_record"],
            review_required=True,
            operation=event["operation"],
            expected_sha256=event["expected_sha256"],
            content_inspector=content_inspector,
        )
        prepared.append((event["event_id"], candidate))
    return prepared


def _candidate_bindings(
    prepared: list[tuple[str, dict[str, Any]]],
) -> list[dict[str, str]]:
    return [
        {
            "event_id": event_id,
            "candidate_id": candidate["id"],
            "candidate_request_sha256": candidate["request_sha256"],
            "proposed_record_sha256": candidate["proposed_record_sha256"],
        }
        for event_id, candidate in prepared
    ]


def _new_checkpoint(envelope: dict[str, Any]) -> dict[str, Any]:
    request = envelope["request"]
    source = request["source"]
    checkpoint: dict[str, Any] = {
        "schema_version": 1,
        "kind": "capture_checkpoint",
        "id": _checkpoint_id(source),
        "source": source,
        "source_sha256": _source_sha256(source),
        "cursor": request["cursor"]["after"],
        "envelope_id": envelope["id"],
        "updated_at": request["captured_at"],
    }
    checkpoint["checkpoint_sha256"] = sha256_bytes(
        canonical_json_bytes(_checkpoint_payload(checkpoint))
    )
    require_contract(
        checkpoint,
        package=PACKAGE,
        filename="capture-checkpoint.schema.json",
        source="capture-checkpoint",
    )
    return checkpoint


def ingest_capture_request(
    config: ProjectConfig,
    request: dict[str, Any],
    *,
    content_inspector: ContentInspector | None = None,
) -> dict[str, Any]:
    """Persist one cursor-bound capture request as review-only candidates."""

    errors = _validate_request(request, "capture-request")
    if errors:
        raise ContractError("; ".join(errors))
    findings = inspect_content(
        request,
        phase="capture_ingress",
        inspector=content_inspector,
    )
    if any(finding.severity == "block" for finding in findings):
        raise ContractError(
            "capture content was blocked by policy: "
            + "; ".join(finding.code for finding in findings)
        )
    request_sha256 = sha256_bytes(canonical_json_bytes(request))
    envelope_id = f"CAP-{request_sha256}"
    prepared = _prepare_candidates(
        config,
        request,
        envelope_id,
        content_inspector=content_inspector,
    )
    envelope: dict[str, Any] = {
        "schema_version": 1,
        "kind": "capture_envelope",
        "id": envelope_id,
        "request_sha256": request_sha256,
        "request": request,
        "candidates": _candidate_bindings(prepared),
        "content_findings": [finding.as_dict() for finding in findings],
    }
    envelope["envelope_sha256"] = sha256_bytes(
        canonical_json_bytes(_envelope_payload(envelope))
    )
    envelope_errors = _validate_envelope(envelope, "capture-envelope")
    if envelope_errors:
        raise ContractError("; ".join(envelope_errors))

    with project_writer_lock(config):
        _ensure_store(config)
        envelope_path = _envelope_dir(config) / f"{envelope_id}.json"
        existing_envelope: dict[str, Any] | None = None
        if envelope_path.exists():
            existing_envelope = _read_object(envelope_path)
            existing_errors = _validate_envelope(existing_envelope, envelope_path)
            if existing_errors:
                raise RecordError("; ".join(existing_errors))
            if canonical_json_bytes(existing_envelope) != canonical_json_bytes(
                envelope
            ):
                raise RecordError("capture envelope identity collision")

        checkpoint_path, checkpoint = _checkpoint_for(config, request["source"])
        observed_cursor = checkpoint["cursor"] if checkpoint is not None else None
        expected_cursor = request["cursor"]["before"]
        if observed_cursor != expected_cursor and existing_envelope is None:
            raise RecordError(
                "capture cursor precondition failed: inspect the current checkpoint"
            )

        for _, candidate in prepared:
            _persist_candidate_locked(config, candidate)

        if existing_envelope is None:
            atomic_write_json(envelope_path, envelope)
        if observed_cursor == expected_cursor:
            atomic_write_json(checkpoint_path, _new_checkpoint(envelope))
        return envelope


def _scan_directory(
    directory: Path,
    pattern: re.Pattern[str],
) -> tuple[list[Path], list[str]]:
    if not directory.exists():
        return [], []
    if directory.is_symlink() or not directory.is_dir():
        return [], [f"{directory}: capture path must be a real directory"]
    paths: list[Path] = []
    errors: list[str] = []
    for path in sorted(directory.iterdir(), key=lambda item: item.name):
        if (
            path.is_symlink()
            or not path.is_file()
            or pattern.fullmatch(path.name) is None
        ):
            errors.append(f"{path}: unexpected capture store entry")
        else:
            paths.append(path)
    return paths, errors


def _audit_candidate_binding(
    config: ProjectConfig,
    envelope: dict[str, Any],
    binding: dict[str, str],
) -> list[str]:
    path = (
        config.extensions_dir
        / "continuity"
        / "candidates"
        / f"{binding['candidate_id']}.json"
    )
    try:
        candidate = _read_object(path)
    except RecordError as exc:
        return [f"{envelope['id']}: capture candidate is missing: {exc}"]
    errors = validate_contract(
        candidate,
        package="stateweave.continuity",
        filename="memory-candidate.schema.json",
        source=path,
    )
    if candidate.get("id") != binding["candidate_id"]:
        errors.append(f"{path}: candidate id does not match capture binding")
    if candidate.get("request_sha256") != binding["candidate_request_sha256"]:
        errors.append(f"{path}: candidate request digest does not match capture")
    if candidate.get("proposed_record_sha256") != binding["proposed_record_sha256"]:
        errors.append(f"{path}: proposed record digest does not match capture")
    provenance = candidate.get("provenance")
    derivation_ids: list[Any] = []
    if isinstance(provenance, dict):
        observed_derivations = provenance.get("derivation_ids")
        if isinstance(observed_derivations, list):
            derivation_ids = observed_derivations
    if envelope["id"] not in derivation_ids:
        errors.append(f"{path}: candidate does not derive from capture envelope")
    if candidate.get("review_required") is not True:
        errors.append(f"{path}: captured candidate must require review")
    return errors


def _audit_chains(
    envelopes: list[dict[str, Any]],
    checkpoints: dict[str, dict[str, Any]],
) -> list[str]:
    errors: list[str] = []
    by_source: dict[str, list[dict[str, Any]]] = {}
    for envelope in envelopes:
        source_sha256 = _source_sha256(envelope["request"]["source"])
        by_source.setdefault(source_sha256, []).append(envelope)
    for source_sha256, source_envelopes in sorted(by_source.items()):
        by_before: dict[str | None, dict[str, Any]] = {}
        for envelope in source_envelopes:
            before = envelope["request"]["cursor"]["before"]
            if before in by_before:
                errors.append(
                    f"{envelope['id']}: capture cursor chain forks at {before!r}"
                )
            else:
                by_before[before] = envelope
        seen: set[str] = set()
        cursor: str | None = None
        head: dict[str, Any] | None = None
        while cursor in by_before:
            envelope = by_before[cursor]
            if envelope["id"] in seen:
                errors.append(f"{envelope['id']}: capture cursor chain cycles")
                break
            seen.add(envelope["id"])
            head = envelope
            cursor = envelope["request"]["cursor"]["after"]
        if len(seen) != len(source_envelopes):
            errors.append(
                f"capture source {source_sha256}: cursor chain is disconnected"
            )
        checkpoint = checkpoints.get(source_sha256)
        if checkpoint is None:
            errors.append(f"capture source {source_sha256}: checkpoint is missing")
        elif head is not None and (
            checkpoint["cursor"] != head["request"]["cursor"]["after"]
            or checkpoint["envelope_id"] != head["id"]
        ):
            errors.append(
                f"capture source {source_sha256}: checkpoint does not match chain head"
            )
    for source_sha256 in sorted(set(checkpoints) - set(by_source)):
        errors.append(f"capture source {source_sha256}: checkpoint has no envelope")
    return errors


def _orphan_candidate_errors(
    config: ProjectConfig,
    envelope_ids: set[str],
) -> list[str]:
    directory = config.extensions_dir / "continuity" / "candidates"
    if not directory.exists() or directory.is_symlink() or not directory.is_dir():
        return []
    errors: list[str] = []
    for path in sorted(directory.glob("CND-*.json")):
        try:
            candidate = _read_object(path)
        except RecordError:
            continue
        provenance = candidate.get("provenance")
        if not isinstance(provenance, dict):
            continue
        derivations = provenance.get("derivation_ids")
        if not isinstance(derivations, list):
            continue
        for identifier in derivations:
            if (
                isinstance(identifier, str)
                and identifier.startswith("CAP-")
                and identifier not in envelope_ids
            ):
                errors.append(
                    f"{path}: candidate references missing capture envelope {identifier}"
                )
    return errors


def _audit_store(config: ProjectConfig) -> CaptureReport:
    report = CaptureReport()
    root = _root(config)
    if not root.exists():
        return report
    if root.is_symlink() or not root.is_dir():
        report.errors.append(f"{root}: capture root must be a real directory")
        return report
    try:
        observed_root = {path.name for path in root.iterdir()}
    except OSError as exc:
        report.errors.append(f"{root}: cannot inspect capture root: {exc}")
        return report
    for name in sorted({"envelopes", "checkpoints"} - observed_root):
        report.errors.append(f"{root / name}: required capture directory is missing")
    for name in sorted(observed_root - {"envelopes", "checkpoints"}):
        report.errors.append(f"{root / name}: unexpected capture root entry")

    envelope_paths, errors = _scan_directory(_envelope_dir(config), ENVELOPE_NAME)
    report.errors.extend(errors)
    envelopes: list[dict[str, Any]] = []
    for path in envelope_paths:
        try:
            envelope = _read_object(path)
        except RecordError as exc:
            report.errors.append(str(exc))
            continue
        envelope_errors = _validate_envelope(envelope, path)
        report.errors.extend(envelope_errors)
        if path.name != f"{envelope.get('id')}.json":
            report.errors.append(f"{path}: envelope filename does not match id")
        if envelope_errors:
            continue
        findings = inspect_content(envelope["request"], phase="capture_audit")
        for finding in findings:
            message = f"{path}: {finding.code} at {finding.path}"
            if finding.severity == "block":
                report.errors.append(message)
            else:
                report.warnings.append(message)
        for binding in envelope["candidates"]:
            report.errors.extend(_audit_candidate_binding(config, envelope, binding))
            report.candidate_binding_count += 1
        envelopes.append(envelope)
        report.envelope_count += 1

    checkpoint_paths, errors = _scan_directory(
        _checkpoint_dir(config),
        CHECKPOINT_NAME,
    )
    report.errors.extend(errors)
    checkpoints: dict[str, dict[str, Any]] = {}
    for path in checkpoint_paths:
        try:
            checkpoint = _read_object(path)
        except RecordError as exc:
            report.errors.append(str(exc))
            continue
        checkpoint_errors = _validate_checkpoint(checkpoint, path)
        report.errors.extend(checkpoint_errors)
        if path.name != f"{checkpoint.get('id')}.json":
            report.errors.append(f"{path}: checkpoint filename does not match id")
        if checkpoint_errors:
            continue
        source_sha256 = checkpoint["source_sha256"]
        if source_sha256 in checkpoints:
            report.errors.append(f"{path}: duplicate checkpoint source")
        checkpoints[source_sha256] = checkpoint
        report.checkpoint_count += 1
    report.errors.extend(_audit_chains(envelopes, checkpoints))
    report.errors.extend(
        _orphan_candidate_errors(
            config,
            {envelope["id"] for envelope in envelopes},
        )
    )
    report.errors = sorted(set(report.errors))
    report.warnings = sorted(set(report.warnings))
    return report


def audit_capture(config: ProjectConfig) -> CaptureReport:
    """Audit capture envelopes, checkpoints, and candidate bindings."""

    with project_writer_lock(config):
        return _audit_store(config)
