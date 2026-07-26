"""Versioned, project-owned authority and optional-module policy."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from stateweave.contracts import require_contract
from stateweave.core.errors import ContractError


@dataclass(frozen=True)
class PolicyPack:
    identifier: str
    roles: tuple[str, ...]
    allowed_effects: Mapping[str, tuple[str, ...]]
    human_required_effects: tuple[str, ...]
    risk_ceiling_by_role: Mapping[str, str]
    telemetry_enabled: bool
    telemetry_allowed_fields: tuple[str, ...]
    telemetry_retention_days: int


@dataclass(frozen=True)
class AuthorityDecision:
    allowed: bool
    requires_human: bool
    reason: str


def _build_policy_pack(payload: dict[str, Any], source: str | Path) -> PolicyPack:
    require_contract(
        payload,
        package="stateweave.policy",
        filename="policy-pack.schema.json",
        source=source,
    )
    roles = tuple(payload["roles"])
    role_set = frozenset(roles)
    allowed_effects = {
        role: tuple(effects)
        for role, effects in payload["authority"]["allowed_effects"].items()
    }
    risk_ceiling = dict(payload["routing"]["risk_ceiling_by_role"])
    unknown_authority = sorted(set(allowed_effects) - role_set)
    unknown_routing = sorted(set(risk_ceiling) - role_set)
    missing_authority = sorted(role_set - set(allowed_effects))
    missing_routing = sorted(role_set - set(risk_ceiling))
    errors: list[str] = []
    if unknown_authority:
        errors.append(f"authority policy has unknown roles: {unknown_authority}")
    if unknown_routing:
        errors.append(f"routing policy has unknown roles: {unknown_routing}")
    if missing_authority:
        errors.append(f"authority policy is missing roles: {missing_authority}")
    if missing_routing:
        errors.append(f"routing policy is missing roles: {missing_routing}")
    if errors:
        raise ContractError(f"{source}: " + "; ".join(errors))
    return PolicyPack(
        identifier=payload["id"],
        roles=roles,
        allowed_effects=MappingProxyType(allowed_effects),
        human_required_effects=tuple(payload["authority"]["human_required_effects"]),
        risk_ceiling_by_role=MappingProxyType(risk_ceiling),
        telemetry_enabled=payload["telemetry"]["enabled"],
        telemetry_allowed_fields=tuple(payload["telemetry"]["allowed_fields"]),
        telemetry_retention_days=payload["telemetry"]["retention_days"],
    )


def load_policy_pack(path: str | Path) -> PolicyPack:
    """Load a JSON policy pack without applying any external effect."""

    source = Path(path)
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractError(f"cannot read policy pack {source}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ContractError(f"{source}: policy pack must be an object")
    return _build_policy_pack(payload, source)


def authorize_effect(
    policy: PolicyPack,
    *,
    role: str,
    effect: str,
    human_approved: bool = False,
) -> AuthorityDecision:
    """Evaluate local policy; this function never performs the effect."""

    allowed = policy.allowed_effects.get(role)
    if allowed is None:
        return AuthorityDecision(False, False, f"role {role!r} is not configured")
    if effect not in allowed:
        return AuthorityDecision(
            False,
            False,
            f"effect {effect!r} is not allowed for role {role!r}",
        )
    requires_human = effect in policy.human_required_effects
    if requires_human and not human_approved:
        return AuthorityDecision(
            False,
            True,
            f"effect {effect!r} requires explicit human approval",
        )
    return AuthorityDecision(True, requires_human, "policy requirements satisfied")
