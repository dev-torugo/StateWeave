"""Explicit, journaled record migrations."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from stateweave.core.backup import (
    create_backup,
    project_writer_lock,
    restore_backup_over_project,
)
from stateweave.core.config import ProjectConfig
from stateweave.core.errors import MigrationError, RecordError
from stateweave.core.io import (
    atomic_write_json,
    canonical_json_bytes,
    read_json,
    sha256_bytes,
)

CURRENT_RECORD_VERSION = "1.0"


@dataclass(frozen=True)
class PlannedChange:
    path: Path
    before: dict[str, Any]
    after: dict[str, Any]


@dataclass(frozen=True)
class MigrationPlan:
    migration_id: str
    from_version: str
    to_version: str
    changes: tuple[PlannedChange, ...]

    def as_dict(self, root: Path) -> dict[str, Any]:
        return {
            "migration_id": self.migration_id,
            "from_version": self.from_version,
            "to_version": self.to_version,
            "changes": [
                {
                    "path": item.path.relative_to(root).as_posix(),
                    "before_sha256": sha256_bytes(canonical_json_bytes(item.before)),
                    "after_sha256": sha256_bytes(canonical_json_bytes(item.after)),
                }
                for item in self.changes
            ],
        }


def _legacy_fact_to_v1(
    payload: dict[str, Any],
    config: ProjectConfig,
) -> dict[str, Any]:
    required = {
        "id",
        "title",
        "statement",
        "status",
        "domain",
        "verified_at",
        "review_after",
        "confidence",
        "owner",
        "classification",
        "sources",
        "supersedes",
        "superseded_by",
    }
    missing = sorted(required - set(payload))
    if missing:
        raise MigrationError(f"legacy fact is missing fields: {missing}")
    fact_class = str(payload.get("domain", config.default_fact_class))
    if fact_class not in config.ttl_days and fact_class not in config.no_expiry_classes:
        fact_class = config.default_fact_class
    migrated_sources: list[dict[str, Any]] = []
    for item in payload["sources"]:
        if not isinstance(item, dict):
            raise MigrationError("legacy fact source must be an object")
        accessed_at = item.get("accessed_at")
        if isinstance(accessed_at, str) and len(accessed_at) == 10:
            accessed_at = f"{accessed_at}T00:00:00Z"
        migrated_sources.append(
            {
                "uri": item.get("uri", item.get("url")),
                "title": item.get("title", item.get("publisher")),
                "accessed_at": accessed_at,
                "kind": item.get("kind"),
            }
        )
    return {
        "schema_version": CURRENT_RECORD_VERSION,
        "kind": "fact",
        "id": payload["id"],
        "title": payload["title"],
        "statement": payload["statement"],
        "status": payload["status"],
        "domain": payload["domain"],
        "fact_class": fact_class,
        "recorded_at": payload["verified_at"],
        "verified_at": payload["verified_at"]
        if payload["status"] == "verified"
        else None,
        "review_after": payload["review_after"],
        "confidence": payload["confidence"],
        "owner_role": payload["owner"],
        "classification": payload["classification"],
        "sources": migrated_sources,
        "claim": None,
        "references": [],
        "supersedes": list(payload["supersedes"]),
        "superseded_by": payload["superseded_by"],
    }


def plan_migration(
    config: ProjectConfig,
    *,
    from_version: str,
    to_version: str,
) -> MigrationPlan:
    if (from_version, to_version) != ("0.1", CURRENT_RECORD_VERSION):
        raise MigrationError(
            f"unsupported migration {from_version!r} -> {to_version!r}"
        )
    changes: list[PlannedChange] = []
    for path in sorted(config.facts_dir.glob("*.json")):
        try:
            payload = read_json(path, max_bytes=config.limits.max_record_bytes)
        except RecordError as exc:
            raise MigrationError(str(exc)) from exc
        if not isinstance(payload, dict):
            raise MigrationError(f"legacy record must be an object: {path}")
        observed_version = payload.get("schema_version")
        if observed_version == CURRENT_RECORD_VERSION:
            continue
        if observed_version not in {None, from_version}:
            raise MigrationError(
                f"{path.relative_to(config.root)} declares unsupported "
                f"schema_version {observed_version!r}"
            )
        changes.append(
            PlannedChange(
                path=path,
                before=payload,
                after=_legacy_fact_to_v1(payload, config),
            )
        )
    migration_id = (
        f"MIG-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
    )
    return MigrationPlan(
        migration_id=migration_id,
        from_version=from_version,
        to_version=to_version,
        changes=tuple(changes),
    )


def apply_migration(
    config: ProjectConfig,
    plan: MigrationPlan,
    *,
    validate_after: Callable[[], list[str]] | None = None,
    fail_after: int | None = None,
) -> Path:
    """Apply a plan with backup, journal, validation, and rollback."""

    if plan.to_version != CURRENT_RECORD_VERSION:
        raise MigrationError(f"unsupported target version: {plan.to_version}")
    config.migrations_dir.mkdir(parents=True, exist_ok=True)
    journal_path = config.migrations_dir / f"{plan.migration_id}.json"
    with project_writer_lock(config):
        backup = create_backup(
            config,
            label="pre-migration",
            acquire_lock=False,
        )
        journal = {
            "schema_version": 1,
            **plan.as_dict(config.root),
            "status": "applying",
            "backup": backup.relative_to(config.root).as_posix(),
            "started_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        }
        atomic_write_json(journal_path, journal)
        try:
            for index, change in enumerate(plan.changes, start=1):
                current = read_json(
                    change.path,
                    max_bytes=config.limits.max_record_bytes,
                )
                if canonical_json_bytes(current) != canonical_json_bytes(change.before):
                    raise MigrationError(
                        f"record drift before migration: "
                        f"{change.path.relative_to(config.root)}"
                    )
                atomic_write_json(change.path, change.after)
                if fail_after is not None and index >= fail_after:
                    raise MigrationError("injected migration failure")
            validation_errors = validate_after() if validate_after else []
            if validation_errors:
                raise MigrationError(
                    "post-migration validation failed: " + "; ".join(validation_errors)
                )
        except BaseException as exc:
            try:
                restore_backup_over_project(backup, config)
                journal["status"] = "rolled_back"
                journal["error_type"] = type(exc).__name__
                journal["finished_at"] = (
                    datetime.now(UTC).isoformat().replace("+00:00", "Z")
                )
                atomic_write_json(journal_path, journal)
            except BaseException as rollback_exc:
                raise MigrationError(
                    f"migration failed and rollback failed; backup is {backup}: "
                    f"{rollback_exc}"
                ) from exc
            if isinstance(exc, MigrationError):
                raise
            raise MigrationError(
                f"migration failed and was rolled back: {exc}"
            ) from exc
        journal["status"] = "complete"
        journal["finished_at"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        atomic_write_json(journal_path, journal)
        return journal_path
