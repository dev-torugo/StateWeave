from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from stateweave.core.config import ProjectConfig
from stateweave.core.io import atomic_write_json
from stateweave.core.project import initialize_project


def project(root: Path, identifier: str = "synthetic-one") -> ProjectConfig:
    return initialize_project(
        root,
        project_id=identifier,
        project_name=identifier.replace("-", " ").title(),
    )


def fact(
    identifier: str,
    *,
    status: str = "verified",
    value: Any = "value",
    verified_at: str = "2026-07-20T12:00:00Z",
    review_after: str | None = "2026-08-15",
    supersedes: list[str] | None = None,
    superseded_by: str | None = None,
    references: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "kind": "fact",
        "id": identifier,
        "title": f"Synthetic fact {identifier}",
        "statement": "Synthetic statement for validation.",
        "status": status,
        "domain": "synthetic",
        "fact_class": "general",
        "recorded_at": "2026-07-20T12:00:00Z",
        "verified_at": verified_at if status == "verified" else None,
        "review_after": review_after,
        "confidence": "high",
        "owner_role": "maintainer",
        "classification": "internal",
        "sources": [
            {
                "uri": "https://example.invalid/synthetic",
                "title": "Synthetic primary source",
                "accessed_at": "2026-07-20T12:00:00Z",
                "kind": "primary",
            }
        ],
        "claim": {
            "subject": "synthetic-service",
            "predicate": "availability",
            "scope": "test",
            "object": value,
        },
        "references": references or [],
        "supersedes": supersedes or [],
        "superseded_by": superseded_by,
    }


def decision(identifier: str) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "kind": "decision",
        "id": identifier,
        "title": f"Synthetic decision {identifier}",
        "status": "accepted",
        "decided_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "decider_role": "maintainer",
        "classification": "internal",
        "context": "Synthetic context.",
        "decision": "Use a synthetic implementation.",
        "consequences": ["Tests remain isolated."],
        "references": [],
        "supersedes": [],
        "superseded_by": None,
    }


def write_fact(config: ProjectConfig, payload: dict[str, Any]) -> Path:
    destination = config.facts_dir / f"{payload['id']}.json"
    atomic_write_json(destination, payload)
    return destination
