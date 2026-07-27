"""Plan and apply non-destructive StateWeave adoption."""

from __future__ import annotations

import os
import re
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from stateweave.contracts import require_contract, validate_contract
from stateweave.core.config import CONFIG_FILENAME, ProjectConfig, load_config
from stateweave.core.errors import ConfigurationError, ContractError, RecordError
from stateweave.core.io import (
    atomic_write_json,
    canonical_json_bytes,
    read_json,
    sha256_bytes,
)
from stateweave.core.project import initialize_project

PACKAGE = "stateweave.adoption"
SIDECAR_DIRECTORY = ".stateweave-project"
ADOPTION_LOCK = ".stateweave-adoption.lock"
STAGING_PREFIX = ".stateweave-adopt-"
MAX_ADOPTION_BYTES = 1024 * 1024


@dataclass
class AdoptionReport:
    """Audit result for sidecar identity and adoption receipts."""

    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    deployment_mode: str = "embedded"
    receipt_count: int = 0

    @property
    def ok(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "errors": sorted(set(self.errors)),
            "warnings": sorted(set(self.warnings)),
            "deployment_mode": self.deployment_mode,
            "receipt_count": self.receipt_count,
        }


def _entry_kind(path: Path) -> str:
    if path.is_symlink():
        return "symlink"
    if path.is_dir():
        return "directory"
    if path.is_file():
        return "file"
    return "other"


def _root_snapshot(
    root: Path,
    *,
    ignored_names: frozenset[str] = frozenset(),
) -> tuple[int, str]:
    entries = [
        {"name": path.name, "kind": _entry_kind(path)}
        for path in sorted(root.iterdir(), key=lambda item: item.name)
        if path.name not in ignored_names
    ]
    return len(entries), sha256_bytes(canonical_json_bytes(entries))


def _plan_payload(plan: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in plan.items() if key != "plan_sha256"}


def _validated_existing_config(
    root: Path,
    project_id: str,
    project_name: str,
) -> tuple[str, str | None, str | None]:
    embedded = root / CONFIG_FILENAME
    sidecar = root / SIDECAR_DIRECTORY
    sidecar_config = sidecar / CONFIG_FILENAME
    candidates = [
        ("embedded", embedded, root),
        ("sidecar", sidecar_config, sidecar),
    ]
    existing = [item for item in candidates if item[1].exists()]
    if len(existing) > 1:
        return (
            "blocked",
            None,
            "both embedded and sidecar StateWeave configurations exist",
        )
    if not existing:
        return "absent", None, None
    mode, config_path, config_root = existing[0]
    if config_path.is_symlink() or not config_path.is_file():
        return "blocked", None, f"{config_path.name} is not a real file"
    if mode == "sidecar" and (sidecar.is_symlink() or not sidecar.is_dir()):
        return "blocked", None, "the StateWeave sidecar is not a real directory"
    try:
        config = load_config(config_root)
    except ConfigurationError:
        return "blocked", None, "the existing StateWeave configuration is invalid"
    if config.project_id != project_id or config.project_name != project_name:
        return (
            "blocked",
            None,
            "the requested identity differs from the existing StateWeave project",
        )
    relative = config.source.relative_to(root).as_posix()
    return "already_adopted", mode, relative


def plan_project_adoption(
    destination: str | Path,
    *,
    project_id: str,
    project_name: str,
) -> dict[str, Any]:
    """Return a read-only, hash-bound sidecar adoption plan."""

    supplied = Path(destination)
    conflicts: list[dict[str, str]] = []
    warnings: list[str] = []
    existing_count = 0
    snapshot_sha256 = sha256_bytes(canonical_json_bytes([]))
    status = "blocked"
    deployment_mode: str | None = None
    config_path: str | None = None
    planned_writes: list[str] = []
    preserved_count = 0

    if supplied.is_symlink():
        conflicts.append(
            {
                "path": ".",
                "code": "symlink-root",
                "message": "adoption root may not be a symlink",
            }
        )
    elif not supplied.exists():
        conflicts.append(
            {
                "path": ".",
                "code": "missing-root",
                "message": "adoption requires an existing project directory",
            }
        )
    elif not supplied.is_dir():
        conflicts.append(
            {
                "path": ".",
                "code": "invalid-root",
                "message": "adoption root must be a real directory",
            }
        )
    else:
        root = supplied.resolve()
        try:
            existing_count, snapshot_sha256 = _root_snapshot(root)
        except OSError:
            conflicts.append(
                {
                    "path": ".",
                    "code": "unreadable-root",
                    "message": "adoption root could not be inspected",
                }
            )
        else:
            interrupted = sorted(
                path.name
                for path in root.iterdir()
                if path.name == ADOPTION_LOCK or path.name.startswith(STAGING_PREFIX)
            )
            for name in interrupted:
                conflicts.append(
                    {
                        "path": name,
                        "code": "interrupted-adoption",
                        "message": "preserve and inspect interrupted adoption evidence",
                    }
                )
            existing_status, existing_mode, existing_config = (
                _validated_existing_config(root, project_id, project_name)
            )
            if existing_status == "already_adopted" and not conflicts:
                status = "already_adopted"
                deployment_mode = existing_mode
                config_path = existing_config
                preserved_count = existing_count
            elif existing_status == "blocked":
                conflicts.append(
                    {
                        "path": CONFIG_FILENAME,
                        "code": "existing-stateweave-conflict",
                        "message": existing_config
                        or "existing StateWeave state is ambiguous",
                    }
                )
            else:
                sidecar = root / SIDECAR_DIRECTORY
                if sidecar.exists() or sidecar.is_symlink():
                    conflicts.append(
                        {
                            "path": SIDECAR_DIRECTORY,
                            "code": "sidecar-conflict",
                            "message": "the sidecar path already exists",
                        }
                    )
                elif not conflicts:
                    status = "safe"
                    deployment_mode = "sidecar"
                    config_path = f"{SIDECAR_DIRECTORY}/{CONFIG_FILENAME}"
                    planned_writes = [SIDECAR_DIRECTORY]
                    preserved_count = existing_count

    if conflicts:
        status = "blocked"
        deployment_mode = None
        config_path = None
        planned_writes = []
        preserved_count = 0

    plan: dict[str, Any] = {
        "schema_version": 1,
        "kind": "adoption_plan",
        "status": status,
        "project_id": project_id,
        "project_name": project_name,
        "deployment_mode": deployment_mode,
        "config_path": config_path,
        "existing_entry_count": existing_count,
        "existing_snapshot_sha256": snapshot_sha256,
        "preserved_entry_count": preserved_count,
        "planned_writes": planned_writes,
        "conflicts": conflicts,
        "warnings": warnings,
    }
    plan["plan_sha256"] = sha256_bytes(canonical_json_bytes(_plan_payload(plan)))
    require_contract(
        plan,
        package=PACKAGE,
        filename="adoption-plan.schema.json",
        source="adoption-plan",
    )
    return plan


def discover_project_config(path: str | Path) -> Path:
    """Resolve an embedded or adopted sidecar configuration without ambiguity."""

    supplied = Path(path)
    if supplied.is_symlink():
        raise ConfigurationError("StateWeave config discovery may not follow a symlink")
    if supplied.is_file():
        return supplied
    embedded = supplied / CONFIG_FILENAME
    sidecar_root = supplied / SIDECAR_DIRECTORY
    sidecar = sidecar_root / CONFIG_FILENAME
    if embedded.is_symlink():
        raise ConfigurationError("embedded StateWeave config may not be a symlink")
    if sidecar_root.is_symlink() or sidecar.is_symlink():
        raise ConfigurationError("StateWeave sidecar may not be a symlink")
    present = [candidate for candidate in (embedded, sidecar) if candidate.exists()]
    if len(present) > 1:
        raise ConfigurationError(
            "both embedded and sidecar StateWeave configurations exist"
        )
    return present[0] if present else supplied


def _receipt_payload(receipt: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in receipt.items() if key != "receipt_sha256"}


def _write_adoption_receipt(
    config: ProjectConfig,
    *,
    plan: dict[str, Any],
    adopted_at: str,
) -> dict[str, Any]:
    receipt: dict[str, Any] = {
        "schema_version": 1,
        "kind": "adoption_receipt",
        "id": f"ADP-{plan['plan_sha256']}",
        "plan_sha256": plan["plan_sha256"],
        "project_id": plan["project_id"],
        "project_name": plan["project_name"],
        "deployment_mode": "sidecar",
        "config_path": f"{SIDECAR_DIRECTORY}/{CONFIG_FILENAME}",
        "existing_entry_count": plan["existing_entry_count"],
        "existing_snapshot_sha256": plan["existing_snapshot_sha256"],
        "preserved_entry_count": plan["preserved_entry_count"],
        "created_paths": [SIDECAR_DIRECTORY],
        "adopted_at": adopted_at,
    }
    receipt["receipt_sha256"] = sha256_bytes(
        canonical_json_bytes(_receipt_payload(receipt))
    )
    require_contract(
        receipt,
        package=PACKAGE,
        filename="adoption-receipt.schema.json",
        source="adoption-receipt",
    )
    directory = config.extensions_dir / "adoption"
    directory.mkdir(parents=True)
    atomic_write_json(directory / f"{receipt['id']}.json", receipt)
    return receipt


def _load_existing_receipt(config: ProjectConfig) -> dict[str, Any] | None:
    directory = config.extensions_dir / "adoption"
    if not directory.exists():
        return None
    if directory.is_symlink() or not directory.is_dir():
        raise RecordError("adoption receipt path must be a real directory")
    entries = sorted(directory.iterdir(), key=lambda item: item.name)
    if len(entries) != 1:
        raise RecordError("adopted sidecar must contain exactly one adoption receipt")
    path = entries[0]
    if (
        path.is_symlink()
        or not path.is_file()
        or re.fullmatch(r"ADP-[a-f0-9]{64}\.json", path.name) is None
    ):
        raise RecordError("adoption receipt store contains an unexpected entry")
    payload = read_json(path, max_bytes=MAX_ADOPTION_BYTES)
    if not isinstance(payload, dict):
        raise RecordError("adoption receipt must be an object")
    errors = validate_contract(
        payload,
        package=PACKAGE,
        filename="adoption-receipt.schema.json",
        source=path,
    )
    if errors:
        raise ContractError("; ".join(errors))
    expected = sha256_bytes(canonical_json_bytes(_receipt_payload(payload)))
    if payload.get("receipt_sha256") != expected:
        raise RecordError("adoption receipt digest does not match")
    if payload.get("id") != f"ADP-{payload.get('plan_sha256')}":
        raise RecordError("adoption receipt id does not match its plan")
    if path.name != f"{payload.get('id')}.json":
        raise RecordError("adoption receipt filename does not match its id")
    return payload


def audit_adoption(config: ProjectConfig) -> AdoptionReport:
    """Audit the optional adoption receipt and its current project identity."""

    report = AdoptionReport(
        deployment_mode=(
            "sidecar" if config.root.name == SIDECAR_DIRECTORY else "embedded"
        )
    )
    try:
        receipt = _load_existing_receipt(config)
    except (ContractError, RecordError) as exc:
        report.errors.append(str(exc))
        return report
    if receipt is None:
        if report.deployment_mode == "sidecar":
            report.errors.append("adopted sidecar is missing its adoption receipt")
        return report
    report.receipt_count = 1
    if receipt["project_id"] != config.project_id:
        report.errors.append("adoption receipt project id does not match config")
    if receipt["project_name"] != config.project_name:
        report.errors.append("adoption receipt project name does not match config")
    report.errors = sorted(set(report.errors))
    return report


def apply_project_adoption(
    destination: str | Path,
    *,
    project_id: str,
    project_name: str,
    expected_plan_sha256: str,
    adopted_at: str,
    confirmed: bool,
) -> dict[str, Any]:
    """Apply one exact adoption plan without overwriting project content."""

    if not confirmed:
        raise RecordError("adoption requires explicit confirmation")
    plan = plan_project_adoption(
        destination,
        project_id=project_id,
        project_name=project_name,
    )
    if plan["plan_sha256"] != expected_plan_sha256:
        raise RecordError("adoption plan changed; inspect a new dry-run")
    root = Path(destination).resolve()
    if plan["status"] == "blocked":
        raise RecordError("adoption plan is blocked")
    if plan["status"] == "already_adopted":
        config = load_config(discover_project_config(root))
        return {
            "status": "already_adopted",
            "config_path": plan["config_path"],
            "receipt": _load_existing_receipt(config),
        }

    lock_path = root / ADOPTION_LOCK
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise RecordError("another or interrupted adoption is present") from exc
    os.close(descriptor)
    staging = root / f"{STAGING_PREFIX}{uuid.uuid4().hex}"
    sidecar = root / SIDECAR_DIRECTORY
    try:
        observed_count, observed_sha256 = _root_snapshot(
            root,
            ignored_names=frozenset({ADOPTION_LOCK}),
        )
        if (
            observed_count != plan["existing_entry_count"]
            or observed_sha256 != plan["existing_snapshot_sha256"]
        ):
            raise RecordError("adoption root changed after acquiring its lock")
        if sidecar.exists() or sidecar.is_symlink():
            raise RecordError("sidecar path changed after adoption planning")
        config = initialize_project(
            staging,
            project_id=project_id,
            project_name=project_name,
        )
        receipt = _write_adoption_receipt(
            config,
            plan=plan,
            adopted_at=adopted_at,
        )
        if sidecar.exists() or sidecar.is_symlink():
            raise RecordError("sidecar path changed during adoption")
        staging.rename(sidecar)
    except BaseException:
        if staging.exists() and not staging.is_symlink():
            shutil.rmtree(staging)
        raise
    finally:
        lock_path.unlink(missing_ok=True)

    return {
        "status": "adopted",
        "config_path": f"{SIDECAR_DIRECTORY}/{CONFIG_FILENAME}",
        "receipt": receipt,
    }
