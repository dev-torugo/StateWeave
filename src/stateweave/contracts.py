"""Shared JSON Schema helpers for optional StateWeave modules."""

from __future__ import annotations

import json
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator  # type: ignore[import-untyped]
from jsonschema.exceptions import SchemaError  # type: ignore[import-untyped]

from stateweave.core.errors import ContractError
from stateweave.core.schema import FORMAT_CHECKER

MODULE_SCHEMAS = {
    "stateweave.adoption": (
        "adoption-plan.schema.json",
        "adoption-receipt.schema.json",
    ),
    "stateweave.adapters": (
        "codex-observation.schema.json",
        "codex-session.schema.json",
    ),
    "stateweave.capture": (
        "capture-checkpoint.schema.json",
        "capture-envelope.schema.json",
        "capture-request.schema.json",
    ),
    "stateweave.continuity": (
        "candidate-rejection.schema.json",
        "episodic-ledger.schema.json",
        "memory-candidate.schema.json",
        "mutation-plan.schema.json",
    ),
    "stateweave.onboarding": (
        "onboarding-plan.schema.json",
        "sidecar-policy-decision.schema.json",
    ),
    "stateweave.context": (
        "context-bundle.schema.json",
        "context-index.schema.json",
        "memory-query-result.schema.json",
        "memory-query.schema.json",
    ),
    "stateweave.orchestration": (
        "evaluation.schema.json",
        "execution-receipt.schema.json",
        "input-manifest.schema.json",
        "task.schema.json",
        "worker.schema.json",
    ),
    "stateweave.policy": ("policy-pack.schema.json",),
    "stateweave.workflow": (
        "acceptance.schema.json",
        "handoff.schema.json",
        "work-request.schema.json",
    ),
}


@lru_cache(maxsize=64)
def _validator(package: str, filename: str) -> Draft202012Validator:
    resource = resources.files(package).joinpath("schemas", filename)
    try:
        schema = json.loads(resource.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractError(
            f"cannot load contract {package}:{filename}: {exc}"
        ) from exc
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise ContractError(
            f"invalid Draft 2020-12 contract {package}:{filename}: {exc.message}"
        ) from exc
    return Draft202012Validator(schema, format_checker=FORMAT_CHECKER)


def validate_contract(
    payload: Any,
    *,
    package: str,
    filename: str,
    source: str | Path,
) -> list[str]:
    """Return deterministic validation errors for one module contract."""

    validator = _validator(package, filename)
    errors = sorted(
        validator.iter_errors(payload),
        key=lambda error: (error.json_path, error.validator or "", error.message),
    )
    return [f"{source}: {error.json_path}: {error.message}" for error in errors]


def require_contract(
    payload: Any,
    *,
    package: str,
    filename: str,
    source: str | Path,
) -> None:
    """Raise a domain error when a module contract does not validate."""

    errors = validate_contract(
        payload,
        package=package,
        filename=filename,
        source=source,
    )
    if errors:
        raise ContractError("; ".join(errors))


def check_module_schemas() -> list[str]:
    """Return deterministic errors for every packaged optional-module schema."""

    errors: list[str] = []
    for package, filenames in sorted(MODULE_SCHEMAS.items()):
        for filename in filenames:
            try:
                _validator(package, filename)
            except ContractError as exc:
                errors.append(str(exc))
    return errors
