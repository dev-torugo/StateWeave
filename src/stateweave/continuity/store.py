"""Local-first persistence for candidates, episodes, and governed write-back."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from stateweave.content import ContentInspector, inspect_content
from stateweave.contracts import require_contract, validate_contract
from stateweave.core.backup import project_writer_lock
from stateweave.core.config import ProjectConfig
from stateweave.core.errors import ContractError, RecordError
from stateweave.core.io import (
    atomic_write_json,
    canonical_json_bytes,
    read_json,
    sha256_bytes,
    sha256_file,
)
from stateweave.core.project import put_record, put_records, record_destination
from stateweave.core.schema import validate_record
from stateweave.core.transactions import transaction_id_for_key
from stateweave.orchestration import audit_execution
from stateweave.workflow import audit_workflow

PACKAGE = "stateweave.continuity"
CANDIDATE_ID = re.compile(r"^CND-[a-f0-9]{64}$")
PLAN_ID = re.compile(r"^MPL-[A-Za-z0-9][A-Za-z0-9._-]{1,127}$")
EPISODE_ID = re.compile(r"^EPS-[a-f0-9]{64}\.json$")
CONTEXT_ID = re.compile(r"^CTX-[a-f0-9]{64}\.json$")
MAX_EXTENSION_BYTES = 16 * 1024 * 1024


@dataclass
class ContinuityReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    candidate_count: int = 0
    rejection_count: int = 0
    context_count: int = 0
    episode_count: int = 0
    plan_count: int = 0

    @property
    def ok(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "errors": sorted(set(self.errors)),
            "warnings": sorted(set(self.warnings)),
            "candidate_count": self.candidate_count,
            "rejection_count": self.rejection_count,
            "context_count": self.context_count,
            "episode_count": self.episode_count,
            "plan_count": self.plan_count,
        }


def _root(config: ProjectConfig) -> Path:
    return config.extensions_dir / "continuity"


def _candidate_dir(config: ProjectConfig) -> Path:
    return _root(config) / "candidates"


def _rejection_dir(config: ProjectConfig) -> Path:
    return _root(config) / "candidate-decisions"


def _context_dir(config: ProjectConfig) -> Path:
    return _root(config) / "contexts"


def _plan_dir(config: ProjectConfig) -> Path:
    return _root(config) / "mutation-plans"


def _episode_dir(config: ProjectConfig, ledger: str) -> Path:
    if ledger not in {"orchestration", "workflow"}:
        raise ValueError(f"unsupported continuity ledger: {ledger}")
    return _root(config) / "episodes" / ledger


def _ensure_store(config: ProjectConfig) -> None:
    for directory in (
        _candidate_dir(config),
        _rejection_dir(config),
        _context_dir(config),
        _plan_dir(config),
        _episode_dir(config, "orchestration"),
        _episode_dir(config, "workflow"),
    ):
        if directory.is_symlink():
            raise RecordError(f"continuity path may not be a symlink: {directory}")
        directory.mkdir(parents=True, exist_ok=True)


def _read_object(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise RecordError(f"continuity artifact must be a real file: {path}")
    payload = read_json(path, max_bytes=MAX_EXTENSION_BYTES)
    if not isinstance(payload, dict):
        raise RecordError(f"continuity artifact must be an object: {path}")
    return payload


def _immutable_write(path: Path, payload: dict[str, Any]) -> Path:
    encoded = canonical_json_bytes(payload)
    if len(encoded) > MAX_EXTENSION_BYTES:
        raise RecordError(f"continuity artifact exceeds size limit: {path}")
    if path.exists():
        if path.is_symlink() or not path.is_file():
            raise RecordError(f"continuity artifact is not a real file: {path}")
        if path.read_bytes() != encoded:
            raise RecordError(f"continuity artifact identity collision: {path.name}")
        return path
    atomic_write_json(path, payload)
    return path


def _context_payload(bundle: dict[str, Any]) -> dict[str, Any]:
    excluded = {"schema_version", "kind", "id", "context_sha256"}
    return {key: value for key, value in bundle.items() if key not in excluded}


def _validate_context(bundle: dict[str, Any], source: str | Path) -> list[str]:
    errors = validate_contract(
        bundle,
        package="stateweave.context",
        filename="context-bundle.schema.json",
        source=source,
    )
    digest = sha256_bytes(canonical_json_bytes(_context_payload(bundle)))
    if bundle.get("context_sha256") != digest:
        errors.append(f"{source}: context digest does not match its payload")
    if bundle.get("id") != f"CTX-{digest}":
        errors.append(f"{source}: context id does not match its payload")
    return errors


def store_context_bundle(config: ProjectConfig, bundle: dict[str, Any]) -> Path:
    """Persist one immutable ContextBundle by its verified digest."""

    errors = _validate_context(bundle, "context-bundle")
    if errors:
        raise ContractError("; ".join(errors))
    with project_writer_lock(config):
        _ensure_store(config)
        path = _context_dir(config) / f"{bundle['id']}.json"
        return _immutable_write(path, bundle)


def _candidate_path(config: ProjectConfig, identifier: str) -> Path:
    if CANDIDATE_ID.fullmatch(identifier) is None:
        raise RecordError(f"invalid candidate id: {identifier!r}")
    return _candidate_dir(config) / f"{identifier}.json"


def _validate_candidate(candidate: dict[str, Any], source: str | Path) -> list[str]:
    errors = validate_contract(
        candidate,
        package=PACKAGE,
        filename="memory-candidate.schema.json",
        source=source,
    )
    proposed = candidate.get("proposed_record")
    if not isinstance(proposed, dict):
        return errors
    identifier = proposed.get("id")
    kind = proposed.get("kind")
    if isinstance(kind, str):
        errors.extend(validate_record(proposed, kind, Path(str(source))))
    if candidate.get("proposed_record_sha256") != sha256_bytes(
        canonical_json_bytes(proposed)
    ):
        errors.append(f"{source}: proposed record digest does not match")
    if candidate.get("classification") != proposed.get("classification"):
        errors.append(f"{source}: candidate and record classifications differ")
    if not isinstance(identifier, str):
        errors.append(f"{source}: proposed record id must be a string")
    return sorted(set(errors))


def _prepare_candidate(
    config: ProjectConfig,
    *,
    idempotency_key: str,
    captured_at: str,
    classification: str,
    confidence: str,
    source: dict[str, Any],
    provenance: dict[str, Any],
    proposed_record: dict[str, Any],
    review_required: bool = True,
    operation: str = "create",
    expected_sha256: str | None = None,
    content_inspector: ContentInspector | None = None,
) -> dict[str, Any]:
    _, key_digest = transaction_id_for_key(idempotency_key)
    findings = inspect_content(
        proposed_record,
        phase="candidate_ingress",
        inspector=content_inspector,
    )
    if any(finding.severity == "block" for finding in findings):
        raise ContractError(
            "candidate content was blocked by policy: "
            + "; ".join(finding.code for finding in findings)
        )
    request = {
        "captured_at": captured_at,
        "classification": classification,
        "confidence": confidence,
        "source": source,
        "provenance": provenance,
        "proposed_record": proposed_record,
        "review_required": review_required,
        "operation": operation,
        "expected_sha256": expected_sha256,
        "content_findings": [finding.as_dict() for finding in findings],
    }
    request_sha256 = sha256_bytes(canonical_json_bytes(request))
    candidate: dict[str, Any] = {
        "schema_version": 1,
        "kind": "memory_candidate",
        "id": f"CND-{key_digest}",
        "status": "pending",
        "request_sha256": request_sha256,
        "idempotency_key_sha256": key_digest,
        **request,
        "proposed_record_sha256": sha256_bytes(canonical_json_bytes(proposed_record)),
        "promotion": None,
    }
    errors = _validate_candidate(candidate, "memory-candidate")
    if classification not in config.policy.allowed_classifications:
        errors.append(f"classification {classification!r} is not allowed")
    if operation == "create" and expected_sha256 is not None:
        errors.append("candidate create operation must expect an absent record")
    if operation == "update" and expected_sha256 is None:
        errors.append("candidate update operation requires expected_sha256")
    if errors:
        raise ContractError("; ".join(sorted(set(errors))))
    return candidate


def _persist_candidate_locked(
    config: ProjectConfig,
    candidate: dict[str, Any],
) -> dict[str, Any]:
    _ensure_store(config)
    path = _candidate_path(config, candidate["id"])
    if path.exists():
        existing = _read_object(path)
        existing_errors = _validate_candidate(existing, path)
        if existing_errors:
            raise RecordError("; ".join(existing_errors))
        if existing["request_sha256"] != candidate["request_sha256"]:
            raise RecordError(
                "candidate idempotency key was reused for a different request"
            )
        return existing
    atomic_write_json(path, candidate)
    return candidate


def capture_candidate(
    config: ProjectConfig,
    *,
    idempotency_key: str,
    captured_at: str,
    classification: str,
    confidence: str,
    source: dict[str, Any],
    provenance: dict[str, Any],
    proposed_record: dict[str, Any],
    review_required: bool = True,
    operation: str = "create",
    expected_sha256: str | None = None,
    content_inspector: ContentInspector | None = None,
) -> dict[str, Any]:
    """Capture one untrusted candidate without promoting it to canonical memory."""

    candidate = _prepare_candidate(
        config,
        idempotency_key=idempotency_key,
        captured_at=captured_at,
        classification=classification,
        confidence=confidence,
        source=source,
        provenance=provenance,
        proposed_record=proposed_record,
        review_required=review_required,
        operation=operation,
        expected_sha256=expected_sha256,
        content_inspector=content_inspector,
    )
    with project_writer_lock(config):
        return _persist_candidate_locked(config, candidate)


def _load_candidate(config: ProjectConfig, identifier: str) -> dict[str, Any]:
    path = _candidate_path(config, identifier)
    candidate = _read_object(path)
    errors = _validate_candidate(candidate, path)
    if errors:
        raise RecordError("; ".join(errors))
    return candidate


def _candidate_sha256(candidate: dict[str, Any]) -> str:
    return sha256_bytes(canonical_json_bytes(candidate))


def _rejection_payload(decision: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in decision.items()
        if key not in {"id", "decision_sha256"}
    }


def _rejection_path(config: ProjectConfig, candidate_id: str) -> Path:
    if CANDIDATE_ID.fullmatch(candidate_id) is None:
        raise RecordError(f"invalid candidate id: {candidate_id!r}")
    return _rejection_dir(config) / f"{candidate_id}.json"


def _validate_rejection(
    decision: dict[str, Any],
    source: str | Path,
) -> list[str]:
    errors = validate_contract(
        decision,
        package=PACKAGE,
        filename="candidate-rejection.schema.json",
        source=source,
    )
    digest = sha256_bytes(canonical_json_bytes(_rejection_payload(decision)))
    if decision.get("decision_sha256") != digest:
        errors.append(f"{source}: candidate rejection digest does not match")
    if decision.get("id") != f"CRJ-{digest}":
        errors.append(f"{source}: candidate rejection id does not match")
    return sorted(set(errors))


def _load_rejection(
    config: ProjectConfig,
    candidate_id: str,
) -> dict[str, Any] | None:
    path = _rejection_path(config, candidate_id)
    if not path.exists():
        return None
    decision = _read_object(path)
    errors = _validate_rejection(decision, path)
    if errors:
        raise RecordError("; ".join(errors))
    return decision


def _effective_situation(
    config: ProjectConfig,
    candidate: dict[str, Any],
) -> str:
    rejection = _load_rejection(config, candidate["id"])
    if rejection is not None:
        if rejection["candidate_sha256"] != _candidate_sha256(candidate):
            return "blocked-rejection-drift"
        return "rejected"
    if candidate["status"] == "rejected":
        return "rejected"
    if candidate["status"] == "promoted":
        return "promoted"

    proposed = candidate["proposed_record"]
    destination = record_destination(config, proposed["id"])
    if destination.is_symlink():
        return "blocked-target"
    if not destination.exists():
        return (
            "pending"
            if candidate["operation"] == "create"
            else "blocked-missing-record"
        )
    if not destination.is_file():
        return "blocked-target"
    observed = sha256_file(destination)
    if observed == candidate["proposed_record_sha256"]:
        return "promotion-needs-reconciliation"
    if candidate["operation"] == "create":
        return "blocked-current-record"
    if observed == candidate["expected_sha256"]:
        return "pending"
    return "blocked-stale-revision"


def _preview_candidate_unlocked(
    config: ProjectConfig,
    candidate_id: str,
) -> dict[str, Any]:
    candidate = _load_candidate(config, candidate_id)
    proposed = candidate["proposed_record"]
    destination = record_destination(config, proposed["id"])
    current: dict[str, Any] | None = None
    current_sha256: str | None = None
    if destination.is_file() and not destination.is_symlink():
        current_payload = read_json(
            destination,
            max_bytes=config.limits.max_record_bytes,
        )
        if isinstance(current_payload, dict):
            current = current_payload
            current_sha256 = sha256_file(destination)
    keys = set(proposed)
    if current is not None:
        keys.update(current)
    changed_fields = sorted(
        key for key in keys if current is None or current.get(key) != proposed.get(key)
    )
    preview = {
        "candidate_id": candidate_id,
        "candidate_sha256": _candidate_sha256(candidate),
        "status": candidate["status"],
        "effective_situation": _effective_situation(config, candidate),
        "operation": candidate["operation"],
        "record_id": proposed["id"],
        "expected_sha256": candidate["expected_sha256"],
        "current_sha256": current_sha256,
        "proposed_sha256": candidate["proposed_record_sha256"],
        "changed_fields": changed_fields,
        "review_required": candidate["review_required"],
        "content_findings": candidate["content_findings"],
    }
    preview["preview_sha256"] = sha256_bytes(canonical_json_bytes(preview))
    return preview


def preview_candidate(
    config: ProjectConfig,
    candidate_id: str,
) -> dict[str, Any]:
    """Return a read-only, hash-bound top-level diff for human review."""

    with project_writer_lock(config):
        return _preview_candidate_unlocked(config, candidate_id)


def list_candidates(
    config: ProjectConfig,
    *,
    situation: str | None = None,
    classification: str | None = None,
    confidence: str | None = None,
    operation: str | None = None,
    source_type: str | None = None,
    review_required: bool | None = None,
) -> dict[str, Any]:
    """List the Candidate Inbox with deterministic filters and derived state."""

    with project_writer_lock(config):
        directory = _candidate_dir(config)
        if not directory.exists():
            candidates: list[dict[str, Any]] = []
        elif directory.is_symlink() or not directory.is_dir():
            raise RecordError("candidate store must be a real directory")
        else:
            candidates = []
            for path in sorted(directory.iterdir(), key=lambda item: item.name):
                if path.is_symlink() or not path.is_file():
                    raise RecordError(f"unexpected candidate store entry: {path}")
                candidate = _load_candidate(config, path.stem)
                effective = _effective_situation(config, candidate)
                if situation is not None and effective != situation:
                    continue
                if (
                    classification is not None
                    and candidate["classification"] != classification
                ):
                    continue
                if confidence is not None and candidate["confidence"] != confidence:
                    continue
                if operation is not None and candidate["operation"] != operation:
                    continue
                if (
                    source_type is not None
                    and candidate["source"]["type"] != source_type
                ):
                    continue
                if (
                    review_required is not None
                    and candidate["review_required"] is not review_required
                ):
                    continue
                rejection = _load_rejection(config, candidate["id"])
                candidates.append(
                    {
                        "candidate_id": candidate["id"],
                        "candidate_sha256": _candidate_sha256(candidate),
                        "stored_status": candidate["status"],
                        "effective_situation": effective,
                        "captured_at": candidate["captured_at"],
                        "classification": candidate["classification"],
                        "confidence": candidate["confidence"],
                        "operation": candidate["operation"],
                        "record_id": candidate["proposed_record"]["id"],
                        "source_type": candidate["source"]["type"],
                        "review_required": candidate["review_required"],
                        "rejection_id": (
                            rejection["id"] if rejection is not None else None
                        ),
                    }
                )
    filters = {
        "situation": situation,
        "classification": classification,
        "confidence": confidence,
        "operation": operation,
        "source_type": source_type,
        "review_required": review_required,
    }
    return {"count": len(candidates), "filters": filters, "candidates": candidates}


def reject_candidate(
    config: ProjectConfig,
    candidate_id: str,
    *,
    expected_preview_sha256: str,
    reason_code: str,
    reviewer_role: str,
    decided_at: str,
    human_approved: bool = False,
) -> dict[str, Any]:
    """Persist one immutable, candidate-bound human rejection decision."""

    if not human_approved:
        raise ContractError("candidate rejection requires explicit human approval")
    if reviewer_role not in config.roles:
        raise ContractError(f"reviewer role {reviewer_role!r} is not configured")
    with project_writer_lock(config):
        candidate = _load_candidate(config, candidate_id)
        if candidate["status"] == "promoted":
            raise ContractError(f"candidate {candidate_id} was already promoted")
        existing = _load_rejection(config, candidate_id)
        if existing is not None:
            replay = {
                "preview_sha256": expected_preview_sha256,
                "reason_code": reason_code,
                "reviewer_role": reviewer_role,
                "decided_at": decided_at,
            }
            observed = {key: existing[key] for key in replay}
            if observed != replay:
                raise RecordError(
                    "candidate rejection replay differs from immutable decision"
                )
            return existing
        preview = _preview_candidate_unlocked(config, candidate_id)
        if preview["preview_sha256"] != expected_preview_sha256:
            raise RecordError("candidate preview changed; inspect a new preview")
        if preview["effective_situation"] == "promotion-needs-reconciliation":
            raise ContractError(
                f"candidate {candidate_id} promotion requires reconciliation"
            )
        decision: dict[str, Any] = {
            "schema_version": 1,
            "kind": "candidate_rejection",
            "candidate_id": candidate_id,
            "candidate_sha256": preview["candidate_sha256"],
            "preview_sha256": preview["preview_sha256"],
            "decision": "reject",
            "reason_code": reason_code,
            "reviewer_role": reviewer_role,
            "decided_at": decided_at,
        }
        digest = sha256_bytes(canonical_json_bytes(_rejection_payload(decision)))
        decision["id"] = f"CRJ-{digest}"
        decision["decision_sha256"] = digest
        errors = _validate_rejection(decision, "candidate-rejection")
        if errors:
            raise ContractError("; ".join(errors))
        _ensure_store(config)
        _immutable_write(_rejection_path(config, candidate_id), decision)
        return decision


def promote_candidate(
    config: ProjectConfig,
    candidate_id: str,
    *,
    reviewer_role: str,
    promoted_at: str,
    expected_preview_sha256: str | None = None,
    human_approved: bool = False,
    content_inspector: ContentInspector | None = None,
) -> dict[str, Any]:
    """Promote a reviewed candidate through the durable core transaction path."""

    if reviewer_role not in config.roles:
        raise ContractError(f"reviewer role {reviewer_role!r} is not configured")
    if not human_approved:
        raise ContractError("candidate promotion requires explicit human approval")

    with project_writer_lock(config):
        candidate = _load_candidate(config, candidate_id)
        if candidate["status"] == "promoted":
            return candidate
        if expected_preview_sha256 is None:
            raise ContractError("candidate promotion requires a reviewed preview")
        if _load_rejection(config, candidate_id) is not None:
            raise ContractError(f"candidate {candidate_id} was rejected")
        if candidate["status"] == "rejected":
            raise ContractError(f"candidate {candidate_id} was rejected")
        preview = _preview_candidate_unlocked(config, candidate_id)
        if preview["preview_sha256"] != expected_preview_sha256:
            raise RecordError("candidate preview changed; inspect a new preview")
        findings = inspect_content(
            candidate["proposed_record"],
            phase="candidate_promotion",
            inspector=content_inspector,
        )
        if any(finding.severity == "block" for finding in findings):
            raise ContractError("candidate content is blocked by promotion policy")
        proposed = candidate["proposed_record"]
        identifier = proposed["id"]
        promotion_key = f"promote:{candidate_id}"
        transaction_id, _ = transaction_id_for_key(promotion_key)
        destination = put_record(
            config,
            proposed,
            overwrite=candidate["operation"] == "update",
            expected_sha256=candidate["expected_sha256"],
            idempotency_key=promotion_key,
            acquire_lock=False,
        )
        record_sha256 = sha256_file(destination)

        candidate["status"] = "promoted"
        candidate["promotion"] = {
            "record_id": identifier,
            "record_sha256": record_sha256,
            "transaction_id": transaction_id,
            "reviewer_role": reviewer_role,
            "promoted_at": promoted_at,
        }
        errors = _validate_candidate(
            candidate,
            _candidate_path(config, candidate_id),
        )
        if errors:
            raise RecordError("; ".join(errors))
        atomic_write_json(_candidate_path(config, candidate_id), candidate)
        return candidate


def _episode_payload(kind: str, documents: list[dict[str, Any]]) -> dict[str, Any]:
    ordered = sorted(documents, key=lambda item: (str(item.get("id", "")), str(item)))
    return {
        "schema_version": 1,
        "kind": f"{kind}_ledger",
        "documents_sha256": sha256_bytes(canonical_json_bytes(ordered)),
        "documents": ordered,
    }


def _episode_paths(config: ProjectConfig, kind: str) -> list[Path]:
    directory = _episode_dir(config, kind)
    if not directory.exists():
        return []
    if directory.is_symlink() or not directory.is_dir():
        raise RecordError(f"episode store must be a real directory: {directory}")
    paths: list[Path] = []
    for path in sorted(directory.iterdir(), key=lambda item: item.name):
        if (
            path.is_symlink()
            or not path.is_file()
            or not EPISODE_ID.fullmatch(path.name)
        ):
            raise RecordError(f"unexpected episode store entry: {path}")
        paths.append(path)
    return paths


def _load_episode_documents(
    config: ProjectConfig,
    kind: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    documents: list[dict[str, Any]] = []
    errors: list[str] = []
    try:
        paths = _episode_paths(config, kind)
    except RecordError as exc:
        return [], [str(exc)]
    for path in paths:
        try:
            episode = _read_object(path)
        except RecordError as exc:
            errors.append(str(exc))
            continue
        errors.extend(
            validate_contract(
                episode,
                package=PACKAGE,
                filename="episodic-ledger.schema.json",
                source=path,
            )
        )
        if episode.get("kind") != f"{kind}_ledger":
            errors.append(f"{path}: episode is stored in the wrong ledger")
        episode_documents = episode.get("documents")
        if not isinstance(episode_documents, list):
            continue
        digest = sha256_bytes(canonical_json_bytes(episode_documents))
        if episode.get("documents_sha256") != digest:
            errors.append(f"{path}: episode document digest does not match")
        if path.name != f"EPS-{digest}.json":
            errors.append(f"{path}: episode filename does not match its digest")
        documents.extend(item for item in episode_documents if isinstance(item, dict))
    return documents, errors


def _documents_by_id(documents: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for document in documents:
        identifier = document.get("id")
        if not isinstance(identifier, str):
            continue
        existing = by_id.get(identifier)
        if existing is not None and existing != document:
            raise ContractError(f"episodic document identity collision: {identifier}")
        by_id[identifier] = document
    return by_id


def _context_exists(config: ProjectConfig, digest: str) -> bool:
    path = _context_dir(config) / f"CTX-{digest}.json"
    if not path.is_file() or path.is_symlink():
        return False
    try:
        bundle = _read_object(path)
    except RecordError:
        return False
    return not _validate_context(bundle, path) and bundle["context_sha256"] == digest


def _receipt_context_errors(
    config: ProjectConfig,
    documents: Iterable[dict[str, Any]],
) -> list[str]:
    errors: list[str] = []
    for document in documents:
        if document.get("kind") != "execution_receipt":
            continue
        identifier = document.get("id", "receipt")
        digest = document.get("context_sha256")
        if not isinstance(digest, str):
            errors.append(f"{identifier}: persistent receipt requires context_sha256")
        elif not _context_exists(config, digest):
            errors.append(f"{identifier}: missing verified ContextBundle {digest}")
    return errors


def _append_episode(
    config: ProjectConfig,
    kind: str,
    documents: list[dict[str, Any]],
) -> Path:
    if not documents:
        raise ContractError("episodic append requires at least one document")
    with project_writer_lock(config):
        _ensure_store(config)
        existing, load_errors = _load_episode_documents(config, kind)
        if load_errors:
            raise RecordError("; ".join(load_errors))
        merged = _documents_by_id(existing)
        incoming = _documents_by_id(documents)
        new_documents: list[dict[str, Any]] = []
        for identifier, document in incoming.items():
            if identifier in merged and merged[identifier] != document:
                raise ContractError(
                    f"episodic document identity collision: {identifier}"
                )
            if identifier not in merged:
                new_documents.append(document)
            merged[identifier] = document
        materialized = [merged[key] for key in sorted(merged)]
        if kind == "orchestration":
            execution_report = audit_execution(materialized)
            errors = execution_report.errors + _receipt_context_errors(
                config, materialized
            )
        else:
            workflow_report = audit_workflow(materialized, roles=config.roles)
            errors = workflow_report.errors
        if errors:
            raise ContractError("; ".join(sorted(set(errors))))
        if not new_documents:
            paths = _episode_paths(config, kind)
            if not paths:
                raise RecordError("episodic replay has no durable source episode")
            return paths[0]
        episode = _episode_payload(kind, new_documents)
        require_contract(
            episode,
            package=PACKAGE,
            filename="episodic-ledger.schema.json",
            source=f"{kind}-episode",
        )
        path = _episode_dir(config, kind) / f"EPS-{episode['documents_sha256']}.json"
        return _immutable_write(path, episode)


def append_orchestration_documents(
    config: ProjectConfig,
    documents: list[dict[str, Any]],
) -> Path:
    """Atomically append a valid orchestration episode bound to stored context."""

    return _append_episode(config, "orchestration", documents)


def load_orchestration_documents(
    config: ProjectConfig,
) -> tuple[dict[str, Any], ...]:
    """Load a validated, context-bound orchestration snapshot."""

    with project_writer_lock(config):
        documents, errors = _load_episode_documents(config, "orchestration")
        report = audit_execution(documents)
        errors.extend(report.errors)
        errors.extend(_receipt_context_errors(config, documents))
        if errors:
            raise RecordError("; ".join(sorted(set(errors))))
        return tuple(
            dict(document)
            for document in sorted(
                documents,
                key=lambda item: (str(item.get("id", "")), str(item)),
            )
        )


def append_workflow_documents(
    config: ProjectConfig,
    documents: list[dict[str, Any]],
) -> Path:
    """Atomically append one complete workflow episode."""

    return _append_episode(config, "workflow", documents)


def _plan_path(config: ProjectConfig, identifier: str) -> Path:
    if PLAN_ID.fullmatch(identifier) is None:
        raise RecordError(f"invalid mutation plan id: {identifier!r}")
    return _plan_dir(config) / f"{identifier}.json"


def _validate_plan(plan: dict[str, Any], source: str | Path) -> list[str]:
    errors = validate_contract(
        plan,
        package=PACKAGE,
        filename="mutation-plan.schema.json",
        source=source,
    )
    seen: set[str] = set()
    for change in plan.get("changes", []):
        if not isinstance(change, dict):
            continue
        identifier = change.get("record_id")
        proposed = change.get("proposed_record")
        operation = change.get("operation")
        expected = change.get("expected_sha256")
        if isinstance(identifier, str):
            if identifier in seen:
                errors.append(f"{source}: duplicate mutation record {identifier}")
            seen.add(identifier)
        if not isinstance(proposed, dict):
            continue
        if proposed.get("id") != identifier:
            errors.append(f"{source}: proposed record id does not match {identifier}")
        kind = proposed.get("kind")
        if isinstance(kind, str):
            errors.extend(validate_record(proposed, kind, Path(str(source))))
        digest = sha256_bytes(canonical_json_bytes(proposed))
        if change.get("proposed_record_sha256") != digest:
            errors.append(f"{source}: proposed record digest does not match")
        if operation == "create" and expected is not None:
            errors.append(f"{source}: create must expect an absent record")
        if operation != "create" and expected is None:
            errors.append(f"{source}: {operation} requires expected_sha256")
        if operation == "state_update" and identifier != "STATE-current":
            errors.append(f"{source}: state_update requires STATE-current")
    return sorted(set(errors))


def _orchestration_by_id(config: ProjectConfig) -> dict[str, dict[str, Any]]:
    documents, errors = _load_episode_documents(config, "orchestration")
    if errors:
        raise RecordError("; ".join(errors))
    return _documents_by_id(documents)


def _validate_plan_evidence(config: ProjectConfig, plan: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    documents = _orchestration_by_id(config)
    receipt = documents.get(plan["source_receipt_id"])
    evaluation = documents.get(plan["evaluation_id"])
    if receipt is None or receipt.get("kind") != "execution_receipt":
        errors.append("mutation plan source receipt is missing")
    elif receipt.get("context_sha256") != plan["context_sha256"]:
        errors.append("mutation plan context differs from its receipt")
    if evaluation is None or evaluation.get("kind") != "evaluation":
        errors.append("mutation plan evaluation is missing")
    elif evaluation.get("receipt_id") != plan["source_receipt_id"]:
        errors.append("mutation plan evaluation belongs to another receipt")
    elif evaluation.get("outcome") != "pass":
        errors.append("mutation plan requires a passing evaluation")
    if not _context_exists(config, plan["context_sha256"]):
        errors.append("mutation plan ContextBundle is missing")
    return errors


def store_mutation_plan(
    config: ProjectConfig,
    plan: dict[str, Any],
    *,
    content_inspector: ContentInspector | None = None,
) -> Path:
    """Persist an immutable proposed write-back plan after evidence checks."""

    errors = _validate_plan(plan, "mutation-plan")
    if plan.get("status") != "proposed":
        errors.append("new mutation plan must have proposed status")
    for change in plan.get("changes", []):
        if not isinstance(change, dict):
            continue
        findings = inspect_content(
            change.get("proposed_record"),
            phase="mutation_plan_ingress",
            inspector=content_inspector,
        )
        if any(finding.severity == "block" for finding in findings):
            errors.append(f"{change.get('record_id')}: content was blocked by policy")
        if findings and plan.get("requires_human") is not True:
            errors.append("content findings require a human-gated mutation plan")
    with project_writer_lock(config):
        _ensure_store(config)
        errors.extend(_validate_plan_evidence(config, plan))
        if errors:
            raise ContractError("; ".join(sorted(set(errors))))
        return _immutable_write(_plan_path(config, plan["id"]), plan)


def _load_plan(config: ProjectConfig, identifier: str) -> dict[str, Any]:
    path = _plan_path(config, identifier)
    plan = _read_object(path)
    errors = _validate_plan(plan, path)
    if errors:
        raise RecordError("; ".join(errors))
    return plan


def apply_mutation_plan(
    config: ProjectConfig,
    plan_id: str,
    *,
    reviewer_role: str,
    applied_at: str,
    human_approved: bool = False,
    content_inspector: ContentInspector | None = None,
) -> dict[str, Any]:
    """Apply one evidence-bound plan through the durable core transaction."""

    plan = _load_plan(config, plan_id)
    if reviewer_role not in config.roles:
        raise ContractError(f"reviewer role {reviewer_role!r} is not configured")
    if plan["status"] == "rejected":
        raise ContractError(f"mutation plan {plan_id} was rejected")
    if plan["requires_human"] and not human_approved:
        raise ContractError("mutation plan requires explicit human approval")
    for change in plan["changes"]:
        findings = inspect_content(
            change["proposed_record"],
            phase="mutation_plan_apply",
            inspector=content_inspector,
        )
        if any(finding.severity == "block" for finding in findings):
            raise ContractError(
                f"{change['record_id']}: content is blocked by apply policy"
            )
        if findings and not human_approved:
            raise ContractError("content findings require explicit human approval")
    payloads = [change["proposed_record"] for change in plan["changes"]]
    expected = {
        change["record_id"]: change["expected_sha256"] for change in plan["changes"]
    }
    mutation_key = f"mutation:{plan_id}"
    transaction_id, _ = transaction_id_for_key(mutation_key)
    destinations = put_records(
        config,
        payloads,
        overwrite=True,
        expected_sha256_by_id=expected,
        idempotency_key=mutation_key,
    )
    results = {
        payload["id"]: sha256_file(destination)
        for payload, destination in zip(payloads, destinations)
    }

    with project_writer_lock(config):
        current = _load_plan(config, plan_id)
        if current["status"] == "applied":
            if current["result_sha256_by_id"] != results:
                raise RecordError("applied mutation plan result drifted")
            return current
        current["status"] = "applied"
        current["reviewer_role"] = reviewer_role
        current["applied_at"] = applied_at
        current["transaction_id"] = transaction_id
        current["result_sha256_by_id"] = results
        errors = _validate_plan(current, _plan_path(config, plan_id))
        if errors:
            raise RecordError("; ".join(errors))
        atomic_write_json(_plan_path(config, plan_id), current)
        return current


def _scan_json_directory(
    directory: Path,
    pattern: re.Pattern[str],
) -> tuple[list[Path], list[str]]:
    if not directory.exists():
        return [], []
    if directory.is_symlink() or not directory.is_dir():
        return [], [f"{directory}: continuity path must be a real directory"]
    paths: list[Path] = []
    errors: list[str] = []
    for path in sorted(directory.iterdir(), key=lambda item: item.name):
        if (
            path.is_symlink()
            or not path.is_file()
            or pattern.fullmatch(path.name) is None
        ):
            errors.append(f"{path}: unexpected continuity store entry")
        else:
            paths.append(path)
    return paths, errors


def _audit_store(config: ProjectConfig) -> ContinuityReport:
    report = ContinuityReport()
    root = _root(config)
    if not root.exists():
        return report
    expected_root = {
        "candidate-decisions",
        "candidates",
        "contexts",
        "mutation-plans",
        "episodes",
    }
    if root.is_symlink() or not root.is_dir():
        report.errors.append(f"{root}: continuity root must be a real directory")
        return report
    try:
        observed_root = {path.name for path in root.iterdir()}
    except OSError as exc:
        report.errors.append(f"{root}: cannot inspect continuity root: {exc}")
        return report
    for name in sorted(observed_root - expected_root):
        report.errors.append(f"{root / name}: unexpected continuity root entry")

    candidate_paths, errors = _scan_json_directory(
        _candidate_dir(config),
        re.compile(r"^CND-[a-f0-9]{64}\.json$"),
    )
    report.errors.extend(errors)
    candidates_by_id: dict[str, dict[str, Any]] = {}
    for path in candidate_paths:
        try:
            candidate = _read_object(path)
        except RecordError as exc:
            report.errors.append(str(exc))
            continue
        report.errors.extend(_validate_candidate(candidate, path))
        findings = inspect_content(
            candidate.get("proposed_record"),
            phase="candidate_audit",
        )
        for finding in findings:
            message = f"{path}: {finding.code} at {finding.path}"
            if finding.severity == "block":
                report.errors.append(message)
            else:
                report.warnings.append(message)
        if path.name != f"{candidate.get('id')}.json":
            report.errors.append(f"{path}: candidate filename does not match id")
        if isinstance(candidate.get("id"), str):
            candidates_by_id[candidate["id"]] = candidate
        if candidate.get("status") == "promoted":
            promotion = candidate.get("promotion")
            if isinstance(promotion, dict):
                try:
                    destination = record_destination(config, promotion["record_id"])
                    observed = sha256_file(destination)
                except (KeyError, OSError, RecordError) as exc:
                    report.errors.append(f"{path}: promoted record is missing: {exc}")
                else:
                    if observed != promotion.get("record_sha256"):
                        report.errors.append(f"{path}: promoted record has drifted")
        elif candidate.get("status") == "pending":
            proposed = candidate.get("proposed_record")
            if isinstance(proposed, dict) and isinstance(proposed.get("id"), str):
                try:
                    destination = record_destination(config, proposed["id"])
                except RecordError:
                    pass
                else:
                    expected = candidate.get("proposed_record_sha256")
                    if destination.is_file() and sha256_file(destination) == expected:
                        report.warnings.append(
                            f"{path}: pending candidate result already exists; "
                            "rerun promotion to reconcile"
                        )
        report.candidate_count += 1

    rejection_paths, errors = _scan_json_directory(
        _rejection_dir(config),
        re.compile(r"^CND-[a-f0-9]{64}\.json$"),
    )
    report.errors.extend(errors)
    for path in rejection_paths:
        try:
            decision = _read_object(path)
        except RecordError as exc:
            report.errors.append(str(exc))
            continue
        report.errors.extend(_validate_rejection(decision, path))
        candidate_id = decision.get("candidate_id")
        rejected_candidate = (
            candidates_by_id.get(candidate_id)
            if isinstance(candidate_id, str)
            else None
        )
        if rejected_candidate is None:
            report.errors.append(f"{path}: rejected candidate is missing")
        else:
            if path.name != f"{rejected_candidate['id']}.json":
                report.errors.append(
                    f"{path}: candidate rejection filename does not match"
                )
            if decision.get("candidate_sha256") != _candidate_sha256(
                rejected_candidate
            ):
                report.errors.append(f"{path}: rejected candidate digest has drifted")
            if rejected_candidate.get("status") == "promoted":
                report.errors.append(
                    f"{path}: promoted candidate also has a rejection decision"
                )
            proposed = rejected_candidate.get("proposed_record")
            if isinstance(proposed, dict) and isinstance(proposed.get("id"), str):
                try:
                    destination = record_destination(config, proposed["id"])
                except RecordError:
                    pass
                else:
                    expected = rejected_candidate.get("proposed_record_sha256")
                    if (
                        destination.is_file()
                        and not destination.is_symlink()
                        and sha256_file(destination) == expected
                    ):
                        report.errors.append(
                            f"{path}: rejected candidate result already exists"
                        )
        report.rejection_count += 1

    context_paths, errors = _scan_json_directory(_context_dir(config), CONTEXT_ID)
    report.errors.extend(errors)
    for path in context_paths:
        try:
            bundle = _read_object(path)
        except RecordError as exc:
            report.errors.append(str(exc))
            continue
        report.errors.extend(_validate_context(bundle, path))
        report.context_count += 1

    orchestration, errors = _load_episode_documents(config, "orchestration")
    report.errors.extend(errors)
    workflow, errors = _load_episode_documents(config, "workflow")
    report.errors.extend(errors)
    execution_report = audit_execution(orchestration)
    workflow_report = audit_workflow(workflow, roles=config.roles)
    report.errors.extend(execution_report.errors)
    report.errors.extend(workflow_report.errors)
    report.errors.extend(_receipt_context_errors(config, orchestration))
    try:
        report.episode_count = len(_episode_paths(config, "orchestration")) + len(
            _episode_paths(config, "workflow")
        )
    except RecordError as exc:
        report.errors.append(str(exc))

    plan_paths, errors = _scan_json_directory(
        _plan_dir(config),
        re.compile(r"^MPL-[A-Za-z0-9][A-Za-z0-9._-]{1,127}\.json$"),
    )
    report.errors.extend(errors)
    for path in plan_paths:
        try:
            plan = _read_object(path)
        except RecordError as exc:
            report.errors.append(str(exc))
            continue
        report.errors.extend(_validate_plan(plan, path))
        try:
            report.errors.extend(_validate_plan_evidence(config, plan))
        except (KeyError, RecordError) as exc:
            report.errors.append(f"{path}: cannot validate plan evidence: {exc}")
        for change in plan.get("changes", []):
            if not isinstance(change, dict):
                continue
            findings = inspect_content(
                change.get("proposed_record"),
                phase="mutation_plan_audit",
            )
            for finding in findings:
                message = (
                    f"{path}: {change.get('record_id')} {finding.code} "
                    f"at {finding.path}"
                )
                if finding.severity == "block":
                    report.errors.append(message)
                else:
                    report.warnings.append(message)
        if plan.get("status") == "applied":
            for identifier, expected in (plan.get("result_sha256_by_id") or {}).items():
                try:
                    observed = sha256_file(record_destination(config, identifier))
                except (OSError, RecordError) as exc:
                    report.errors.append(f"{path}: applied result is missing: {exc}")
                else:
                    if observed != expected:
                        report.errors.append(
                            f"{path}: applied result {identifier} drifted"
                        )
        elif plan.get("status") == "proposed":
            matching = 0
            changes = [
                item for item in plan.get("changes", []) if isinstance(item, dict)
            ]
            for change in changes:
                identifier = change.get("record_id")
                expected = change.get("proposed_record_sha256")
                if not isinstance(identifier, str) or not isinstance(expected, str):
                    continue
                try:
                    destination = record_destination(config, identifier)
                except RecordError:
                    continue
                if destination.is_file() and sha256_file(destination) == expected:
                    matching += 1
            if changes and matching == len(changes):
                report.warnings.append(
                    f"{path}: proposed plan results already exist; "
                    "rerun apply-plan to reconcile"
                )
        report.plan_count += 1
    report.errors = sorted(set(report.errors))
    report.warnings = sorted(set(report.warnings))
    return report


def audit_continuity(config: ProjectConfig) -> ContinuityReport:
    """Audit closed-world continuity artifacts from one consistent snapshot."""

    with project_writer_lock(config):
        return _audit_store(config)
