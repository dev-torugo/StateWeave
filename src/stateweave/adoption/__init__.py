"""Safe sidecar adoption for existing projects."""

from __future__ import annotations

from stateweave.adoption.project import (
    AdoptionReport,
    apply_project_adoption,
    audit_adoption,
    discover_project_config,
    plan_project_adoption,
)

__all__ = [
    "AdoptionReport",
    "apply_project_adoption",
    "audit_adoption",
    "discover_project_config",
    "plan_project_adoption",
]
