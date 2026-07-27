"""Persistent, policy-aware host bridge for externally executed Codex sessions.

The bridge prepares and reconciles evidence. It never launches Codex, approves
an effect, infers a receipt, or claims that an external effect occurred.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from stateweave.adapters.codex import CodexAdapter
from stateweave.content import ContentInspector, inspect_content
from stateweave.context import compile_context
from stateweave.continuity import (
    append_orchestration_documents,
    load_orchestration_documents,
    store_context_bundle,
)
from stateweave.contracts import validate_contract
from stateweave.core.backup import project_writer_lock
from stateweave.core.config import ProjectConfig
from stateweave.core.errors import ContractError, RecordError
from stateweave.core.io import (
    atomic_write_json,
    canonical_json_bytes,
    read_json,
    sha256_bytes,
)
from stateweave.orchestration import manifest_digest, route_task
from stateweave.policy import PolicyPack, authorize_effect
from stateweave.runtime import DispatchEnvelope
from stateweave.runtime.model import SLUG

PACKAGE = "stateweave.adapters"
MAX_BRIDGE_BYTES = 16 * 1024 * 1024
SESSION_FILE = re.compile(r"^SES-[a-f0-9]{64}\.json$")
OBSERVATION_FILE = re.compile(r"^OBS-[a-f0-9]{64}\.json$")


@dataclass
class CodexBridgeReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    session_count: int = 0
    observation_count: int = 0

    @property
    def ok(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "session_count": self.session_count,
            "observation_count": self.observation_count,
            "errors": sorted(set(self.errors)),
            "warnings": sorted(set(self.warnings)),
        }


def _root(config: ProjectConfig) -> Path:
    return config.extensions_dir / "adapters" / "codex"


def _session_dir(config: ProjectConfig) -> Path:
    return _root(config) / "sessions"


def _observation_dir(config: ProjectConfig) -> Path:
    return _root(config) / "observations"


def _ensure_store(config: ProjectConfig) -> None:
    for boundary in (
        config.extensions_dir,
        config.extensions_dir / "adapters",
        _root(config),
    ):
        if boundary.is_symlink():
            raise RecordError(f"Codex bridge path may not be a symlink: {boundary}")
    for directory in (_session_dir(config), _observation_dir(config)):
        if directory.is_symlink():
            raise RecordError(f"Codex bridge path may not be a symlink: {directory}")
        directory.mkdir(parents=True, exist_ok=True)


def _read_object(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise RecordError(f"Codex bridge artifact must be a real file: {path}")
    payload = read_json(path, max_bytes=MAX_BRIDGE_BYTES)
    if not isinstance(payload, dict):
        raise RecordError(f"Codex bridge artifact must be an object: {path}")
    return payload


def _immutable_write(path: Path, payload: dict[str, Any]) -> Path:
    encoded = canonical_json_bytes(payload)
    if len(encoded) > MAX_BRIDGE_BYTES:
        raise RecordError(f"Codex bridge artifact exceeds size limit: {path}")
    if path.exists():
        if path.is_symlink() or not path.is_file():
            raise RecordError(f"Codex bridge artifact is not a real file: {path}")
        if path.read_bytes() != encoded:
            raise RecordError(f"Codex bridge artifact identity collision: {path.name}")
        return path
    atomic_write_json(path, payload)
    return path


def _policy_digest(policy: PolicyPack) -> str:
    payload = {
        "id": policy.identifier,
        "roles": sorted(policy.roles),
        "allowed_effects": {
            role: sorted(effects)
            for role, effects in sorted(policy.allowed_effects.items())
        },
        "human_required_effects": sorted(policy.human_required_effects),
        "risk_ceiling_by_role": dict(sorted(policy.risk_ceiling_by_role.items())),
        "telemetry": {
            "enabled": policy.telemetry_enabled,
            "allowed_fields": sorted(policy.telemetry_allowed_fields),
            "retention_days": policy.telemetry_retention_days,
        },
    }
    return sha256_bytes(canonical_json_bytes(payload))


def _digest_payload(payload: dict[str, Any], excluded: set[str]) -> str:
    material = {key: value for key, value in payload.items() if key not in excluded}
    return sha256_bytes(canonical_json_bytes(material))


def _validate_bound_documents(
    task: dict[str, Any],
    input_manifest: dict[str, Any],
    worker: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    errors.extend(
        validate_contract(
            task,
            package="stateweave.orchestration",
            filename="task.schema.json",
            source="codex-session.task",
        )
    )
    errors.extend(
        validate_contract(
            input_manifest,
            package="stateweave.orchestration",
            filename="input-manifest.schema.json",
            source="codex-session.input_manifest",
        )
    )
    errors.extend(
        validate_contract(
            worker,
            package="stateweave.orchestration",
            filename="worker.schema.json",
            source="codex-session.worker",
        )
    )
    task_id = task.get("id")
    if input_manifest.get("task_id") != task_id:
        errors.append("Codex session input manifest belongs to another task")
    if task.get("input_manifest_id") != input_manifest.get("id"):
        errors.append("Codex session task references another input manifest")
    if worker.get("runtime_adapter") != "codex":
        errors.append("Codex session worker must use the codex runtime adapter")
    try:
        route_task(task, [worker])
    except (ContractError, KeyError, TypeError) as exc:
        errors.append(f"Codex session worker is not eligible: {exc}")
    return errors


def _validate_context(context: dict[str, Any]) -> list[str]:
    errors = validate_contract(
        context,
        package="stateweave.context",
        filename="context-bundle.schema.json",
        source="codex-session.context",
    )
    digest = _digest_payload(
        context,
        {"schema_version", "kind", "id", "context_sha256"},
    )
    if context.get("context_sha256") != digest:
        errors.append("Codex session ContextBundle digest does not match")
    if context.get("id") != f"CTX-{digest}":
        errors.append("Codex session ContextBundle id does not match")
    return errors


def _validate_session(session: dict[str, Any], source: str | Path) -> list[str]:
    errors = validate_contract(
        session,
        package=PACKAGE,
        filename="codex-session.schema.json",
        source=source,
    )
    digest = _digest_payload(
        session,
        {"schema_version", "kind", "id", "session_sha256"},
    )
    if session.get("session_sha256") != digest:
        errors.append(f"{source}: Codex session digest does not match")
    if session.get("id") != f"SES-{digest}":
        errors.append(f"{source}: Codex session id does not match")

    context = session.get("context")
    task = session.get("task")
    input_manifest = session.get("input_manifest")
    worker = session.get("worker")
    if isinstance(context, dict):
        errors.extend(_validate_context(context))
        if session.get("context_id") != context.get("id"):
            errors.append(f"{source}: context_id differs from embedded context")
        if session.get("context_sha256") != context.get("context_sha256"):
            errors.append(f"{source}: context_sha256 differs from embedded context")
    if (
        isinstance(task, dict)
        and isinstance(input_manifest, dict)
        and isinstance(worker, dict)
    ):
        errors.extend(_validate_bound_documents(task, input_manifest, worker))
        dispatch = session.get("dispatch")
        if isinstance(dispatch, dict):
            if dispatch.get("task_id") != task.get("id"):
                errors.append(f"{source}: dispatch belongs to another task")
            if dispatch.get("input_manifest_sha256") != manifest_digest(input_manifest):
                errors.append(f"{source}: dispatch input manifest digest differs")

    requested = session.get("requested_effects")
    decisions = session.get("authority_decisions")
    if isinstance(requested, list) and isinstance(decisions, list):
        decision_effects = [
            item.get("effect") for item in decisions if isinstance(item, dict)
        ]
        if len(decision_effects) != len(set(decision_effects)):
            errors.append(f"{source}: duplicate authority effect decision")
        if set(decision_effects) != set(requested):
            errors.append(
                f"{source}: authority decisions do not cover requested effects"
            )
        ready = all(
            item.get("policy_allowed") is True
            for item in decisions
            if isinstance(item, dict)
        )
        if session.get("ready_for_host") is not ready:
            errors.append(f"{source}: ready_for_host differs from policy decisions")
        dispatch = session.get("dispatch")
        if isinstance(dispatch, dict):
            allowed = {
                item.get("effect")
                for item in decisions
                if isinstance(item, dict) and item.get("policy_allowed") is True
            }
            if set(dispatch.get("allowed_effects", [])) != allowed:
                errors.append(f"{source}: dispatch allowed_effects differ from policy")
            if dispatch.get("execution_authorized") is not False:
                errors.append(f"{source}: adapter may not authorize execution")
    return sorted(set(errors))


def prepare_codex_session(
    config: ProjectConfig,
    *,
    policy: PolicyPack,
    query: dict[str, Any],
    task: dict[str, Any],
    input_manifest: dict[str, Any],
    worker: dict[str, Any],
    role: str,
    requested_effects: tuple[str, ...] = (),
    approval_references: Mapping[str, str] | None = None,
    created_at: str,
    content_inspector: ContentInspector | None = None,
) -> dict[str, Any]:
    """Persist a context-bound host envelope without executing or authorizing it."""

    errors = _validate_bound_documents(task, input_manifest, worker)
    if role not in config.roles or role not in policy.roles:
        errors.append(f"Codex session role {role!r} is not configured")
    if worker.get("role") != role:
        errors.append("Codex session role differs from its worker role")
    if len(set(requested_effects)) != len(requested_effects):
        errors.append("Codex session requested effects must be unique")
    if any(
        not isinstance(effect, str) or SLUG.fullmatch(effect) is None
        for effect in requested_effects
    ):
        errors.append("Codex session requested effects must be portable slugs")

    approvals = dict(approval_references or {})
    unknown_approvals = sorted(set(approvals) - set(requested_effects))
    if unknown_approvals:
        errors.append(
            f"approval references target unrequested effects: {unknown_approvals}"
        )
    for effect, reference in approvals.items():
        if (
            not isinstance(reference, str)
            or not reference.strip()
            or len(reference) > 500
        ):
            errors.append(f"approval reference for {effect!r} must be a short string")
        if effect not in policy.human_required_effects:
            errors.append(
                f"approval reference supplied for non-human-gated effect {effect!r}"
            )

    findings = inspect_content(
        {
            "query": query,
            "task": task,
            "input_manifest": input_manifest,
            "worker": worker,
            "approval_references": approvals,
        },
        phase="codex_session_prepare",
        inspector=content_inspector,
    )
    if any(finding.severity == "block" for finding in findings):
        errors.append(
            "Codex session content was blocked by policy: "
            + ", ".join(
                finding.code for finding in findings if finding.severity == "block"
            )
        )
    if errors:
        raise ContractError("; ".join(sorted(set(errors))))

    context = compile_context(config, query, content_inspector=content_inspector)
    authority_decisions: list[dict[str, Any]] = []
    for effect in sorted(requested_effects):
        approval_ref = approvals.get(effect)
        decision = authorize_effect(
            policy,
            role=role,
            effect=effect,
            human_approved=approval_ref is not None,
        )
        authority_decisions.append(
            {
                "effect": effect,
                "policy_allowed": decision.allowed,
                "requires_human": effect in policy.human_required_effects,
                "approval_ref": approval_ref,
                "reason": decision.reason,
            }
        )
    ready_for_host = all(item["policy_allowed"] for item in authority_decisions)
    allowed_effects = tuple(
        item["effect"] for item in authority_decisions if item["policy_allowed"] is True
    )
    adapter = CodexAdapter(worker["capabilities"])
    dispatch = adapter.prepare(
        DispatchEnvelope(
            task_id=task["id"],
            objective=task["objective"],
            input_manifest_sha256=manifest_digest(input_manifest),
            allowed_effects=allowed_effects,
            metadata={
                "context-sha256": context["context_sha256"],
                "policy-sha256": _policy_digest(policy),
                "worker-id": worker["id"],
            },
        )
    )
    payload: dict[str, Any] = {
        "created_at": created_at,
        "policy_id": policy.identifier,
        "policy_sha256": _policy_digest(policy),
        "role": role,
        "requested_effects": sorted(requested_effects),
        "authority_decisions": authority_decisions,
        "ready_for_host": ready_for_host,
        "context_id": context["id"],
        "context_sha256": context["context_sha256"],
        "context": context,
        "task": task,
        "input_manifest": input_manifest,
        "worker": worker,
        "dispatch": dispatch,
        "content_findings": [finding.as_dict() for finding in findings],
    }
    session_sha256 = sha256_bytes(canonical_json_bytes(payload))
    session = {
        "schema_version": 1,
        "kind": "codex_session",
        "id": f"SES-{session_sha256}",
        "session_sha256": session_sha256,
        **payload,
    }
    errors = _validate_session(session, "codex-session")
    if errors:
        raise ContractError("; ".join(errors))
    store_context_bundle(config, context)
    with project_writer_lock(config):
        _ensure_store(config)
        _immutable_write(_session_dir(config) / f"{session['id']}.json", session)
    return session


def _load_session(config: ProjectConfig, session_id: str) -> dict[str, Any]:
    if re.fullmatch(r"SES-[a-f0-9]{64}", session_id) is None:
        raise RecordError(f"invalid Codex session id: {session_id!r}")
    path = _session_dir(config) / f"{session_id}.json"
    session = _read_object(path)
    errors = _validate_session(session, path)
    if errors:
        raise RecordError("; ".join(errors))
    return session


def _validate_observed_documents(
    session: dict[str, Any],
    receipt: dict[str, Any],
    evaluation: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    errors.extend(
        validate_contract(
            receipt,
            package="stateweave.orchestration",
            filename="execution-receipt.schema.json",
            source="codex-observation.receipt",
        )
    )
    errors.extend(
        validate_contract(
            evaluation,
            package="stateweave.orchestration",
            filename="evaluation.schema.json",
            source="codex-observation.evaluation",
        )
    )
    task = session["task"]
    worker = session["worker"]
    if receipt.get("session_id") != session["id"]:
        errors.append("Codex receipt belongs to another session")
    if receipt.get("task_id") != task["id"]:
        errors.append("Codex receipt belongs to another task")
    if receipt.get("worker_id") != worker["id"]:
        errors.append("Codex receipt belongs to another worker")
    if receipt.get("input_manifest_sha256") != manifest_digest(
        session["input_manifest"]
    ):
        errors.append("Codex receipt input manifest digest differs from session")
    if receipt.get("context_sha256") != session["context_sha256"]:
        errors.append("Codex receipt context digest differs from session")
    runtime = receipt.get("runtime_observation")
    if not isinstance(runtime, dict) or runtime.get("adapter") != "codex":
        errors.append("Codex receipt must contain an observed codex runtime")
    if evaluation.get("receipt_id") != receipt.get("id"):
        errors.append("Codex evaluation belongs to another receipt")

    effects = receipt.get("effects")
    if not isinstance(effects, list):
        errors.append("Codex receipt requires explicit effect observations")
        effects = []
    effect_names = [item.get("name") for item in effects if isinstance(item, dict)]
    if len(effect_names) != len(set(effect_names)):
        errors.append("Codex receipt effect names must be unique")
    if set(effect_names) != set(session["requested_effects"]):
        errors.append("Codex receipt must account for every requested effect")
    decisions = {
        item["effect"]: item
        for item in session["authority_decisions"]
        if isinstance(item, dict) and isinstance(item.get("effect"), str)
    }
    for effect in effects:
        if not isinstance(effect, dict):
            continue
        decision = decisions.get(effect.get("name"))
        if decision is None:
            continue
        if effect.get("approval_ref") != decision.get("approval_ref"):
            errors.append(
                f"Codex receipt approval reference differs for {effect.get('name')}"
            )
        if effect.get("status") == "succeeded" and not decision["policy_allowed"]:
            errors.append(
                f"Codex receipt reports denied effect {effect.get('name')} as succeeded"
            )

    outputs = receipt.get("outputs")
    output_names = (
        [item.get("name") for item in outputs if isinstance(item, dict)]
        if isinstance(outputs, list)
        else []
    )
    if len(output_names) != len(set(output_names)):
        errors.append("Codex receipt output names must be unique")
    expected_outputs = set(task["expected_outputs"])
    if not set(output_names).issubset(expected_outputs):
        errors.append("Codex receipt contains an unexpected output")
    if receipt.get("status") == "succeeded" and set(output_names) != expected_outputs:
        errors.append("Successful Codex receipt must contain every expected output")
    return sorted(set(errors))


def _validate_observation(
    observation: dict[str, Any],
    source: str | Path,
) -> list[str]:
    errors = validate_contract(
        observation,
        package=PACKAGE,
        filename="codex-observation.schema.json",
        source=source,
    )
    digest = _digest_payload(
        observation,
        {"schema_version", "kind", "id", "observation_sha256"},
    )
    if observation.get("observation_sha256") != digest:
        errors.append(f"{source}: Codex observation digest does not match")
    if observation.get("id") != f"OBS-{digest}":
        errors.append(f"{source}: Codex observation id does not match")
    return sorted(set(errors))


def record_codex_observation(
    config: ProjectConfig,
    session_id: str,
    *,
    receipt: dict[str, Any],
    evaluation: dict[str, Any],
    observer: str,
    observed_at: str,
    content_inspector: ContentInspector | None = None,
) -> dict[str, Any]:
    """Persist only host-reported evidence after session and policy reconciliation."""

    with project_writer_lock(config):
        session = _load_session(config, session_id)
    errors = _validate_observed_documents(session, receipt, evaluation)
    findings = inspect_content(
        {
            "receipt": receipt,
            "evaluation": evaluation,
            "observer": observer,
        },
        phase="codex_observation_ingress",
        inspector=content_inspector,
    )
    if any(finding.severity == "block" for finding in findings):
        errors.append(
            "Codex observation content was blocked by policy: "
            + ", ".join(
                finding.code for finding in findings if finding.severity == "block"
            )
        )
    if errors:
        raise ContractError("; ".join(sorted(set(errors))))

    payload = {
        "session_id": session_id,
        "receipt_id": receipt["id"],
        "receipt_sha256": sha256_bytes(canonical_json_bytes(receipt)),
        "evaluation_id": evaluation["id"],
        "evaluation_sha256": sha256_bytes(canonical_json_bytes(evaluation)),
        "observer": observer,
        "observed_at": observed_at,
        "content_findings": [finding.as_dict() for finding in findings],
    }
    observation_sha256 = sha256_bytes(canonical_json_bytes(payload))
    observation = {
        "schema_version": 1,
        "kind": "codex_observation",
        "id": f"OBS-{observation_sha256}",
        "observation_sha256": observation_sha256,
        **payload,
    }
    errors = _validate_observation(observation, "codex-observation")
    if errors:
        raise ContractError("; ".join(errors))

    append_orchestration_documents(
        config,
        [
            session["task"],
            session["input_manifest"],
            session["worker"],
            receipt,
            evaluation,
        ],
    )
    with project_writer_lock(config):
        _ensure_store(config)
        for path in _scan_directory(_observation_dir(config), OBSERVATION_FILE)[0]:
            existing = _read_object(path)
            if existing.get("receipt_id") == receipt["id"] and existing != observation:
                raise RecordError(
                    f"Codex receipt {receipt['id']} already has another observation"
                )
        _immutable_write(
            _observation_dir(config) / f"{observation['id']}.json",
            observation,
        )
    return observation


def _scan_directory(
    directory: Path,
    pattern: re.Pattern[str],
) -> tuple[list[Path], list[str]]:
    if not directory.exists():
        return [], []
    if directory.is_symlink() or not directory.is_dir():
        return [], [f"{directory}: Codex bridge path must be a real directory"]
    paths: list[Path] = []
    errors: list[str] = []
    for path in sorted(directory.iterdir(), key=lambda item: item.name):
        if (
            path.is_symlink()
            or not path.is_file()
            or pattern.fullmatch(path.name) is None
        ):
            errors.append(f"{path}: unexpected Codex bridge entry")
        else:
            paths.append(path)
    return paths, errors


def audit_codex_bridge(config: ProjectConfig) -> CodexBridgeReport:
    """Audit immutable adapter artifacts and their persistent ledger bindings."""

    report = CodexBridgeReport()
    sessions: dict[str, dict[str, Any]] = {}
    observations: list[tuple[Path, dict[str, Any]]] = []
    with project_writer_lock(config):
        root = _root(config)
        if not root.exists():
            return report
        adapter_root = config.extensions_dir / "adapters"
        if adapter_root.is_symlink() or root.is_symlink() or not root.is_dir():
            report.errors.append(f"{root}: Codex bridge root must be a real directory")
            return report
        observed_root = {path.name for path in root.iterdir()}
        for name in sorted(observed_root - {"sessions", "observations"}):
            report.errors.append(f"{root / name}: unexpected Codex bridge root entry")

        session_paths, errors = _scan_directory(_session_dir(config), SESSION_FILE)
        report.errors.extend(errors)
        for path in session_paths:
            try:
                session = _read_object(path)
            except RecordError as exc:
                report.errors.append(str(exc))
                continue
            report.errors.extend(_validate_session(session, path))
            identifier = session.get("id")
            if isinstance(identifier, str):
                sessions[identifier] = session
            if path.name != f"{identifier}.json":
                report.errors.append(f"{path}: session filename does not match id")
            if session.get("ready_for_host") is not True:
                report.warnings.append(
                    f"{path}: session has denied policy preconditions"
                )
            current_findings = inspect_content(
                {
                    "context": session.get("context"),
                    "task": session.get("task"),
                    "input_manifest": session.get("input_manifest"),
                    "worker": session.get("worker"),
                    "authority_decisions": session.get("authority_decisions"),
                },
                phase="codex_session_audit",
            )
            for finding in current_findings:
                message = f"{path}: {finding.code} at {finding.path}"
                if finding.severity == "block":
                    report.errors.append(message)
                else:
                    report.warnings.append(message)
            context_id = session.get("context_id")
            if isinstance(context_id, str):
                context_path = (
                    config.extensions_dir
                    / "continuity"
                    / "contexts"
                    / f"{context_id}.json"
                )
                if (
                    not context_path.is_file()
                    or context_path.is_symlink()
                    or context_path.read_bytes()
                    != canonical_json_bytes(session.get("context"))
                ):
                    report.errors.append(
                        f"{path}: stored ContextBundle is missing or drifted"
                    )
            report.session_count += 1

        observation_paths, errors = _scan_directory(
            _observation_dir(config),
            OBSERVATION_FILE,
        )
        report.errors.extend(errors)
        for path in observation_paths:
            try:
                observation = _read_object(path)
            except RecordError as exc:
                report.errors.append(str(exc))
                continue
            report.errors.extend(_validate_observation(observation, path))
            if path.name != f"{observation.get('id')}.json":
                report.errors.append(f"{path}: observation filename does not match id")
            observations.append((path, observation))
            report.observation_count += 1

    try:
        orchestration = {
            document["id"]: document
            for document in load_orchestration_documents(config)
            if isinstance(document.get("id"), str)
        }
    except RecordError as exc:
        report.errors.append(str(exc))
        orchestration = {}
    seen_receipts: set[str] = set()
    for path, observation in observations:
        session_id = observation.get("session_id")
        matched_session = (
            sessions.get(session_id) if isinstance(session_id, str) else None
        )
        if matched_session is None:
            report.errors.append(f"{path}: observation references a missing session")
            continue
        receipt_id = observation.get("receipt_id")
        evaluation_id = observation.get("evaluation_id")
        receipt = orchestration.get(receipt_id)
        evaluation = orchestration.get(evaluation_id)
        if not isinstance(receipt, dict):
            report.errors.append(f"{path}: observed receipt is missing from the ledger")
            continue
        if not isinstance(evaluation, dict):
            report.errors.append(
                f"{path}: observed evaluation is missing from the ledger"
            )
            continue
        if receipt_id in seen_receipts:
            report.errors.append(f"{path}: receipt has multiple Codex observations")
        elif isinstance(receipt_id, str):
            seen_receipts.add(receipt_id)
        if observation.get("receipt_sha256") != sha256_bytes(
            canonical_json_bytes(receipt)
        ):
            report.errors.append(f"{path}: observed receipt digest has drifted")
        if observation.get("evaluation_sha256") != sha256_bytes(
            canonical_json_bytes(evaluation)
        ):
            report.errors.append(f"{path}: observed evaluation digest has drifted")
        report.errors.extend(
            _validate_observed_documents(matched_session, receipt, evaluation)
        )
        current_findings = inspect_content(
            {
                "receipt": receipt,
                "evaluation": evaluation,
                "observer": observation.get("observer"),
            },
            phase="codex_observation_audit",
        )
        for finding in current_findings:
            message = f"{path}: {finding.code} at {finding.path}"
            if finding.severity == "block":
                report.errors.append(message)
            else:
                report.warnings.append(message)
        for finding in observation.get("content_findings", []):
            if not isinstance(finding, dict):
                continue
            message = f"{path}: {finding.get('code')} at {finding.get('path')}"
            if finding.get("severity") == "block":
                report.errors.append(message)
            else:
                report.warnings.append(message)

    report.errors = sorted(set(report.errors))
    report.warnings = sorted(set(report.warnings))
    return report
