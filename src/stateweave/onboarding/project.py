"""Conversational onboarding plans backed by immutable local evidence."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from stateweave.adoption import (
    apply_project_adoption,
    discover_project_config,
    plan_project_adoption,
)
from stateweave.contracts import validate_contract
from stateweave.core.backup import project_writer_lock
from stateweave.core.config import ProjectConfig, load_config
from stateweave.core.errors import ContractError, RecordError
from stateweave.core.io import (
    atomic_write_json,
    canonical_json_bytes,
    read_json,
    sha256_bytes,
)

PACKAGE = "stateweave.onboarding"
MAX_ONBOARDING_BYTES = 1024 * 1024
PLAN_FILENAME = re.compile(r"^ONP-[a-f0-9]{64}\.json$")
POLICY_FILENAME = "sidecar-policy.json"


@dataclass
class OnboardingReport:
    """Audit result for persisted onboarding plans and sidecar policy."""

    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    plan_count: int = 0
    policy_decision_count: int = 0

    @property
    def ok(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "errors": sorted(set(self.errors)),
            "warnings": sorted(set(self.warnings)),
            "plan_count": self.plan_count,
            "policy_decision_count": self.policy_decision_count,
        }


def _root(config: ProjectConfig) -> Path:
    return config.extensions_dir / "onboarding"


def _plan_dir(config: ProjectConfig) -> Path:
    return _root(config) / "plans"


def _plan_payload(plan: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value for key, value in plan.items() if key not in {"id", "plan_sha256"}
    }


def _decision_payload(decision: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in decision.items()
        if key not in {"id", "decision_sha256"}
    }


def _read_object(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise RecordError(f"onboarding artifact must be a real file: {path}")
    payload = read_json(path, max_bytes=MAX_ONBOARDING_BYTES)
    if not isinstance(payload, dict):
        raise RecordError(f"onboarding artifact must be an object: {path}")
    return payload


def _immutable_write(path: Path, payload: dict[str, Any]) -> Path:
    encoded = canonical_json_bytes(payload)
    if len(encoded) > MAX_ONBOARDING_BYTES:
        raise RecordError(f"onboarding artifact exceeds size limit: {path}")
    if path.exists():
        if path.is_symlink() or not path.is_file():
            raise RecordError(f"onboarding artifact is not a real file: {path}")
        if path.read_bytes() != encoded:
            raise RecordError(f"onboarding artifact identity collision: {path.name}")
        return path
    atomic_write_json(path, payload)
    return path


def _validate_plan(plan: dict[str, Any], source: str | Path) -> list[str]:
    errors = validate_contract(
        plan,
        package=PACKAGE,
        filename="onboarding-plan.schema.json",
        source=source,
    )
    adoption = plan.get("adoption_plan")
    if isinstance(adoption, dict):
        errors.extend(
            validate_contract(
                adoption,
                package="stateweave.adoption",
                filename="adoption-plan.schema.json",
                source=f"{source}:adoption_plan",
            )
        )
    digest = sha256_bytes(canonical_json_bytes(_plan_payload(plan)))
    if plan.get("plan_sha256") != digest:
        errors.append(f"{source}: onboarding plan digest does not match")
    if plan.get("id") != f"ONP-{digest}":
        errors.append(f"{source}: onboarding plan id does not match")
    return sorted(set(errors))


def _validate_policy(decision: dict[str, Any], source: str | Path) -> list[str]:
    errors = validate_contract(
        decision,
        package=PACKAGE,
        filename="sidecar-policy-decision.schema.json",
        source=source,
    )
    digest = sha256_bytes(canonical_json_bytes(_decision_payload(decision)))
    if decision.get("decision_sha256") != digest:
        errors.append(f"{source}: sidecar policy decision digest does not match")
    if decision.get("id") != f"OBD-{digest}":
        errors.append(f"{source}: sidecar policy decision id does not match")
    return sorted(set(errors))


def _load_policy(config: ProjectConfig) -> dict[str, Any] | None:
    path = _root(config) / POLICY_FILENAME
    if not path.exists():
        return None
    decision = _read_object(path)
    errors = _validate_policy(decision, path)
    if errors:
        raise RecordError("; ".join(errors))
    return decision


def _existing_policy(
    destination: str | Path,
    adoption_plan: dict[str, Any],
) -> tuple[dict[str, Any] | None, str | None]:
    if (
        adoption_plan["status"] != "already_adopted"
        or adoption_plan["deployment_mode"] != "sidecar"
    ):
        return None, None
    try:
        config = load_config(discover_project_config(destination))
        return _load_policy(config), None
    except (ContractError, RecordError) as exc:
        return None, str(exc)


def plan_onboarding(
    destination: str | Path,
    *,
    project_id: str,
    project_name: str,
    sidecar_policy: str,
) -> dict[str, Any]:
    """Return a read-only plan with explicit state, risk, decision, and action."""

    if sidecar_policy not in {"tracked", "local", "defer"}:
        raise RecordError("sidecar policy must be tracked, local, or defer")
    adoption = plan_project_adoption(
        destination,
        project_id=project_id,
        project_name=project_name,
    )
    existing_policy, policy_error = _existing_policy(destination, adoption)
    states = [
        {"code": "adoption-status", "value": adoption["status"]},
        {
            "code": "deployment-mode",
            "value": adoption["deployment_mode"] or "unconfigured",
        },
        {"code": "sidecar-policy", "value": sidecar_policy},
        {"code": "host-content-inspection", "value": "not-performed"},
    ]
    risks: list[dict[str, str]] = [
        {
            "code": "host-content-uninspected",
            "severity": "info",
            "message": "Host project contents were not read or converted into memory.",
        }
    ]
    decisions = [
        {
            "code": "sidecar-disposition",
            "choice": sidecar_policy,
            "basis": "explicit operator input",
        },
        {
            "code": "host-content-capture",
            "choice": "explicit-only",
            "basis": "onboarding never scans project content",
        },
        {
            "code": "vcs-mutation",
            "choice": "prohibited",
            "basis": "the operator owns ignore and tracking configuration",
        },
    ]
    actions: list[dict[str, Any]] = [
        {
            "sequence": 1,
            "code": "review-plan",
            "mutation": "none",
            "path": None,
            "requires_human_confirmation": False,
        }
    ]

    if adoption["status"] == "blocked" or policy_error is not None:
        status = "blocked"
        risks.append(
            {
                "code": (
                    "policy-evidence-invalid"
                    if policy_error is not None
                    else "adoption-conflict"
                ),
                "severity": "block",
                "message": (
                    "Existing onboarding policy evidence is invalid."
                    if policy_error is not None
                    else "The underlying adoption plan contains blocking conflicts."
                ),
            }
        )
        actions.append(
            {
                "sequence": 2,
                "code": "resolve-conflict",
                "mutation": "none",
                "path": None,
                "requires_human_confirmation": True,
            }
        )
    elif sidecar_policy == "defer":
        status = "complete" if adoption["status"] == "already_adopted" else "deferred"
        risks.append(
            {
                "code": "continuity-deferred",
                "severity": "warning",
                "message": "No sidecar will be created and continuity remains unavailable.",
            }
        )
        actions.append(
            {
                "sequence": 2,
                "code": "defer-onboarding",
                "mutation": "none",
                "path": None,
                "requires_human_confirmation": True,
            }
        )
    elif adoption["deployment_mode"] == "embedded":
        status = "complete"
        risks.append(
            {
                "code": "embedded-project",
                "severity": "info",
                "message": "The existing embedded store has no sidecar disposition.",
            }
        )
    elif existing_policy is not None:
        if existing_policy["sidecar_policy"] == sidecar_policy:
            status = "complete"
        else:
            status = "blocked"
            risks.append(
                {
                    "code": "immutable-policy-conflict",
                    "severity": "block",
                    "message": "A different immutable sidecar policy is already recorded.",
                }
            )
    else:
        status = "ready"
        if sidecar_policy == "local":
            risks.append(
                {
                    "code": "local-sidecar-vcs-exposure",
                    "severity": "warning",
                    "message": (
                        "StateWeave will not edit ignore rules; the operator must "
                        "keep a local sidecar out of version control."
                    ),
                }
            )
        if adoption["status"] == "safe":
            actions.append(
                {
                    "sequence": 2,
                    "code": "create-sidecar",
                    "mutation": "create",
                    "path": ".stateweave-project",
                    "requires_human_confirmation": True,
                }
            )
        actions.append(
            {
                "sequence": len(actions) + 1,
                "code": "record-sidecar-policy",
                "mutation": "create",
                "path": (
                    ".stateweave-project/.stateweave/extensions/"
                    "onboarding/sidecar-policy.json"
                ),
                "requires_human_confirmation": True,
            }
        )

    plan: dict[str, Any] = {
        "schema_version": 1,
        "kind": "onboarding_plan",
        "status": status,
        "project_id": project_id,
        "project_name": project_name,
        "sidecar_policy": sidecar_policy,
        "adoption_plan": adoption,
        "states": states,
        "risks": risks,
        "decisions": decisions,
        "actions": actions,
    }
    digest = sha256_bytes(canonical_json_bytes(_plan_payload(plan)))
    plan["id"] = f"ONP-{digest}"
    plan["plan_sha256"] = digest
    errors = _validate_plan(plan, "onboarding-plan")
    if errors:
        raise ContractError("; ".join(errors))
    return plan


def _persist_plan_and_policy(
    config: ProjectConfig,
    *,
    plan: dict[str, Any],
    reviewer_role: str,
    decided_at: str,
) -> dict[str, Any]:
    if reviewer_role not in config.roles:
        raise ContractError(f"reviewer role {reviewer_role!r} is not configured")
    decision: dict[str, Any] = {
        "schema_version": 1,
        "kind": "sidecar_policy_decision",
        "onboarding_plan_sha256": plan["plan_sha256"],
        "adoption_plan_sha256": plan["adoption_plan"]["plan_sha256"],
        "project_id": plan["project_id"],
        "sidecar_policy": plan["sidecar_policy"],
        "reviewer_role": reviewer_role,
        "decided_at": decided_at,
    }
    digest = sha256_bytes(canonical_json_bytes(_decision_payload(decision)))
    decision["id"] = f"OBD-{digest}"
    decision["decision_sha256"] = digest
    errors = _validate_policy(decision, "sidecar-policy-decision")
    if errors:
        raise ContractError("; ".join(errors))

    with project_writer_lock(config):
        root = _root(config)
        plans = _plan_dir(config)
        if root.is_symlink() or plans.is_symlink():
            raise RecordError("onboarding paths may not be symlinks")
        plans.mkdir(parents=True, exist_ok=True)
        _immutable_write(plans / f"{plan['id']}.json", plan)
        _immutable_write(root / POLICY_FILENAME, decision)
    return decision


def apply_onboarding_plan(
    destination: str | Path,
    *,
    project_id: str,
    project_name: str,
    sidecar_policy: str,
    expected_plan_sha256: str,
    decided_at: str,
    reviewer_role: str,
    human_confirmed: bool,
) -> dict[str, Any]:
    """Apply only the exact reviewed plan and persist immutable policy evidence."""

    if not human_confirmed:
        raise RecordError("onboarding requires explicit human confirmation")
    plan = plan_onboarding(
        destination,
        project_id=project_id,
        project_name=project_name,
        sidecar_policy=sidecar_policy,
    )
    if plan["plan_sha256"] != expected_plan_sha256:
        raise RecordError("onboarding plan changed; inspect a new plan")
    if plan["status"] == "blocked":
        raise RecordError("onboarding plan is blocked")
    if plan["status"] == "deferred":
        return {"status": "deferred", "plan": plan, "mutated": False}
    if plan["status"] == "complete":
        config = load_config(discover_project_config(destination))
        return {
            "status": "complete",
            "plan": plan,
            "policy_decision": _load_policy(config),
            "mutated": False,
        }

    adoption = plan["adoption_plan"]
    if adoption["status"] == "safe":
        adoption_result = apply_project_adoption(
            destination,
            project_id=project_id,
            project_name=project_name,
            expected_plan_sha256=adoption["plan_sha256"],
            adopted_at=decided_at,
            confirmed=True,
        )
    else:
        adoption_result = {
            "status": "already_adopted",
            "config_path": adoption["config_path"],
        }
    config = load_config(discover_project_config(destination))
    decision = _persist_plan_and_policy(
        config,
        plan=plan,
        reviewer_role=reviewer_role,
        decided_at=decided_at,
    )
    return {
        "status": "onboarded",
        "plan_id": plan["id"],
        "plan_sha256": plan["plan_sha256"],
        "adoption": adoption_result,
        "policy_decision": decision,
        "mutated": True,
    }


def audit_onboarding(config: ProjectConfig) -> OnboardingReport:
    """Audit closed-world, hash-bound onboarding evidence."""

    report = OnboardingReport()
    root = _root(config)
    if not root.exists():
        if config.root.name == ".stateweave-project":
            report.warnings.append("sidecar has no persisted onboarding policy")
        return report
    if root.is_symlink() or not root.is_dir():
        report.errors.append(f"{root}: onboarding root must be a real directory")
        return report
    observed = {path.name for path in root.iterdir()}
    for name in sorted(observed - {"plans", POLICY_FILENAME}):
        report.errors.append(f"{root / name}: unexpected onboarding root entry")

    plans_by_sha: dict[str, dict[str, Any]] = {}
    directory = _plan_dir(config)
    if directory.exists():
        if directory.is_symlink() or not directory.is_dir():
            report.errors.append(f"{directory}: plan store must be a real directory")
        else:
            for path in sorted(directory.iterdir(), key=lambda item: item.name):
                if (
                    path.is_symlink()
                    or not path.is_file()
                    or PLAN_FILENAME.fullmatch(path.name) is None
                ):
                    report.errors.append(f"{path}: unexpected onboarding plan entry")
                    continue
                try:
                    plan = _read_object(path)
                except RecordError as exc:
                    report.errors.append(str(exc))
                    continue
                errors = _validate_plan(plan, path)
                report.errors.extend(errors)
                if path.name != f"{plan.get('id')}.json":
                    report.errors.append(f"{path}: onboarding plan filename mismatch")
                if not errors and isinstance(plan.get("plan_sha256"), str):
                    plans_by_sha[plan["plan_sha256"]] = plan
                report.plan_count += 1

    policy_path = root / POLICY_FILENAME
    if policy_path.exists():
        try:
            decision = _read_object(policy_path)
        except RecordError as exc:
            report.errors.append(str(exc))
        else:
            errors = _validate_policy(decision, policy_path)
            report.errors.extend(errors)
            plan_sha256 = decision.get("onboarding_plan_sha256")
            referenced = (
                plans_by_sha.get(plan_sha256) if isinstance(plan_sha256, str) else None
            )
            if referenced is None:
                report.errors.append(
                    f"{policy_path}: referenced onboarding plan is missing"
                )
            else:
                if decision.get("project_id") != config.project_id:
                    report.errors.append(
                        f"{policy_path}: project id does not match configuration"
                    )
                if decision.get("sidecar_policy") != referenced.get("sidecar_policy"):
                    report.errors.append(
                        f"{policy_path}: sidecar policy differs from its plan"
                    )
                if decision.get("adoption_plan_sha256") != referenced.get(
                    "adoption_plan", {}
                ).get("plan_sha256"):
                    report.errors.append(
                        f"{policy_path}: adoption plan binding does not match"
                    )
            report.policy_decision_count = 1
    elif config.root.name == ".stateweave-project":
        report.warnings.append("sidecar has no persisted onboarding policy")

    report.errors = sorted(set(report.errors))
    report.warnings = sorted(set(report.warnings))
    return report
