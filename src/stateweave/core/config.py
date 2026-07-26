"""Versioned, project-owned memory-core configuration."""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from stateweave.core.errors import ConfigurationError, PathBoundaryError
from stateweave.core.schema import validate_config

CONFIG_FILENAME = "stateweave.toml"
CONFIG_SCHEMA_VERSION = 1
PROJECT_ID_PATTERN = re.compile(r"^[a-z][a-z0-9-]{2,63}$")


@dataclass(frozen=True)
class PathConfig:
    facts: str
    decisions: str
    state: str
    metadata: str
    backups: str
    migrations: str


@dataclass(frozen=True)
class LimitConfig:
    max_record_bytes: int
    max_records: int
    max_state_items: int
    lock_timeout_seconds: float
    lock_stale_after_seconds: int


@dataclass(frozen=True)
class PolicyConfig:
    allowed_classifications: tuple[str, ...]
    review_warning_days: int
    enforce_ttl_ceiling: bool
    fail_on_stale_verified: bool
    require_reciprocal_supersession: bool
    detect_structured_conflicts: bool


@dataclass(frozen=True)
class ProjectConfig:
    root: Path
    source: Path
    schema_version: int
    project_id: str
    project_name: str
    default_fact_class: str
    paths: PathConfig
    ttl_days: dict[str, int]
    no_expiry_classes: tuple[str, ...]
    roles: tuple[str, ...]
    limits: LimitConfig
    policy: PolicyConfig

    def resolve(self, relative: str) -> Path:
        """Resolve a configured path without allowing root escape."""

        candidate = Path(relative)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise PathBoundaryError(f"path must be project-relative: {relative!r}")
        root = self.root.resolve()
        resolved = (root / candidate).resolve(strict=False)
        if not resolved.is_relative_to(root):
            raise PathBoundaryError(f"path escapes project root: {relative!r}")
        return resolved

    @property
    def facts_dir(self) -> Path:
        return self.resolve(self.paths.facts)

    @property
    def decisions_dir(self) -> Path:
        return self.resolve(self.paths.decisions)

    @property
    def state_file(self) -> Path:
        return self.resolve(self.paths.state)

    @property
    def metadata_dir(self) -> Path:
        return self.resolve(self.paths.metadata)

    @property
    def backups_dir(self) -> Path:
        return self.resolve(self.paths.backups)

    @property
    def migrations_dir(self) -> Path:
        return self.resolve(self.paths.migrations)


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigurationError(f"{label} must be a table")
    return value


def _string(table: dict[str, Any], key: str, label: str) -> str:
    value = table.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError(f"{label}.{key} must be a non-empty string")
    return value


def _positive_int(value: Any, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ConfigurationError(f"{label} must be a positive integer")
    return value


def _positive_number(value: Any, label: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
        raise ConfigurationError(f"{label} must be a positive number")
    return float(value)


def _string_tuple(
    value: Any, label: str, *, allow_empty: bool = False
) -> tuple[str, ...]:
    if not isinstance(value, list) or (not value and not allow_empty):
        raise ConfigurationError(f"{label} must be a non-empty array of strings")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise ConfigurationError(f"{label} must contain non-empty strings")
    if len(set(value)) != len(value):
        raise ConfigurationError(f"{label} must not contain duplicates")
    return tuple(value)


def load_config(path: str | Path) -> ProjectConfig:
    """Load a versioned TOML configuration from a file or project directory."""

    source = Path(path)
    if source.is_dir():
        source = source / CONFIG_FILENAME
    source = source.resolve()
    try:
        payload = tomllib.loads(source.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigurationError(f"configuration not found: {source}") from exc
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigurationError(f"cannot read configuration {source}: {exc}") from exc

    schema_version = payload.get("schema_version")
    if schema_version != CONFIG_SCHEMA_VERSION:
        raise ConfigurationError(
            f"unsupported configuration schema_version {schema_version!r}; "
            f"expected {CONFIG_SCHEMA_VERSION}"
        )
    schema_errors = validate_config(payload, source)
    if schema_errors:
        raise ConfigurationError(
            "configuration does not satisfy the official Draft 2020-12 schema: "
            + "; ".join(schema_errors)
        )
    project = _mapping(payload.get("project"), "project")
    project_id = _string(project, "id", "project")
    if PROJECT_ID_PATTERN.fullmatch(project_id) is None:
        raise ConfigurationError("project.id must match ^[a-z][a-z0-9-]{2,63}$")
    project_name = _string(project, "name", "project")

    paths_payload = _mapping(payload.get("paths"), "paths")
    paths = PathConfig(
        facts=_string(paths_payload, "facts", "paths"),
        decisions=_string(paths_payload, "decisions", "paths"),
        state=_string(paths_payload, "state", "paths"),
        metadata=_string(paths_payload, "metadata", "paths"),
        backups=_string(paths_payload, "backups", "paths"),
        migrations=_string(paths_payload, "migrations", "paths"),
    )

    memory = _mapping(payload.get("memory"), "memory")
    default_fact_class = _string(memory, "default_fact_class", "memory")
    ttl_payload = _mapping(memory.get("ttl_days"), "memory.ttl_days")
    ttl_days = {
        key: _positive_int(value, f"memory.ttl_days.{key}")
        for key, value in ttl_payload.items()
        if isinstance(key, str) and key
    }
    if len(ttl_days) != len(ttl_payload) or not ttl_days:
        raise ConfigurationError("memory.ttl_days must define named TTL classes")
    no_expiry = _string_tuple(
        memory.get("no_expiry_classes", []),
        "memory.no_expiry_classes",
        allow_empty=True,
    )
    if default_fact_class not in ttl_days and default_fact_class not in no_expiry:
        raise ConfigurationError(
            "memory.default_fact_class must exist in ttl_days or no_expiry_classes"
        )
    overlap = set(ttl_days).intersection(no_expiry)
    if overlap:
        raise ConfigurationError(
            f"TTL and no-expiry classes overlap: {sorted(overlap)}"
        )

    roles_payload = _mapping(payload.get("roles"), "roles")
    roles = _string_tuple(roles_payload.get("allowed"), "roles.allowed")

    limits_payload = _mapping(payload.get("limits"), "limits")
    limits = LimitConfig(
        max_record_bytes=_positive_int(
            limits_payload.get("max_record_bytes"), "limits.max_record_bytes"
        ),
        max_records=_positive_int(
            limits_payload.get("max_records"), "limits.max_records"
        ),
        max_state_items=_positive_int(
            limits_payload.get("max_state_items"), "limits.max_state_items"
        ),
        lock_timeout_seconds=_positive_number(
            limits_payload.get("lock_timeout_seconds"),
            "limits.lock_timeout_seconds",
        ),
        lock_stale_after_seconds=_positive_int(
            limits_payload.get("lock_stale_after_seconds"),
            "limits.lock_stale_after_seconds",
        ),
    )

    policy_payload = _mapping(payload.get("policy"), "policy")
    classifications = _string_tuple(
        policy_payload.get("allowed_classifications"),
        "policy.allowed_classifications",
    )
    policy = PolicyConfig(
        allowed_classifications=classifications,
        review_warning_days=_positive_int(
            policy_payload.get("review_warning_days"),
            "policy.review_warning_days",
        ),
        enforce_ttl_ceiling=policy_payload.get("enforce_ttl_ceiling") is True,
        fail_on_stale_verified=policy_payload.get("fail_on_stale_verified") is True,
        require_reciprocal_supersession=(
            policy_payload.get("require_reciprocal_supersession") is True
        ),
        detect_structured_conflicts=(
            policy_payload.get("detect_structured_conflicts") is True
        ),
    )
    for key in (
        "enforce_ttl_ceiling",
        "fail_on_stale_verified",
        "require_reciprocal_supersession",
        "detect_structured_conflicts",
    ):
        if not isinstance(policy_payload.get(key), bool):
            raise ConfigurationError(f"policy.{key} must be boolean")

    config = ProjectConfig(
        root=source.parent,
        source=source,
        schema_version=schema_version,
        project_id=project_id,
        project_name=project_name,
        default_fact_class=default_fact_class,
        paths=paths,
        ttl_days=ttl_days,
        no_expiry_classes=no_expiry,
        roles=roles,
        limits=limits,
        policy=policy,
    )
    resolved = {
        "facts": config.resolve(paths.facts),
        "decisions": config.resolve(paths.decisions),
        "state": config.resolve(paths.state),
        "metadata": config.resolve(paths.metadata),
        "backups": config.resolve(paths.backups),
        "migrations": config.resolve(paths.migrations),
    }
    _validate_path_topology(config.source, resolved)
    return config


def _overlaps(left: Path, right: Path) -> bool:
    return left == right or left.is_relative_to(right) or right.is_relative_to(left)


def _validate_path_topology(source: Path, paths: dict[str, Path]) -> None:
    content_names = ("facts", "decisions")
    if _overlaps(paths["facts"], paths["decisions"]):
        raise ConfigurationError("paths.facts and paths.decisions must not overlap")
    for name in content_names:
        if paths["state"] == paths[name] or paths["state"].is_relative_to(paths[name]):
            raise ConfigurationError(f"paths.state must not be inside paths.{name}")
        if _overlaps(paths["metadata"], paths[name]):
            raise ConfigurationError(f"paths.metadata must not overlap paths.{name}")
    if paths["state"].is_relative_to(paths["metadata"]):
        raise ConfigurationError("paths.state must not be inside paths.metadata")
    if source in paths.values():
        raise ConfigurationError(
            "configured data path must not replace stateweave.toml"
        )
    for name in ("backups", "migrations"):
        if paths[name] == paths["metadata"] or not paths[name].is_relative_to(
            paths["metadata"]
        ):
            raise ConfigurationError(f"paths.{name} must be a child of paths.metadata")
    if _overlaps(paths["backups"], paths["migrations"]):
        raise ConfigurationError("paths.backups and paths.migrations must not overlap")


def render_default_config(project_id: str, project_name: str) -> str:
    """Render the deterministic configuration written by `stateweave init`."""

    if PROJECT_ID_PATTERN.fullmatch(project_id) is None:
        raise ConfigurationError("project_id must match ^[a-z][a-z0-9-]{2,63}$")
    if not project_name.strip():
        raise ConfigurationError("project_name must be a non-empty value")
    encoded_name = json.dumps(project_name, ensure_ascii=False)
    return f'''schema_version = 1

[project]
id = "{project_id}"
name = {encoded_name}

[paths]
facts = "memory/facts"
decisions = "memory/decisions"
state = "memory/state/current.json"
metadata = ".stateweave"
backups = ".stateweave/backups"
migrations = ".stateweave/migrations"

[memory]
default_fact_class = "general"
no_expiry_classes = ["immutable"]

[memory.ttl_days]
volatile = 30
general = 90
policy = 180

[roles]
allowed = ["maintainer", "reviewer", "contributor"]

[limits]
max_record_bytes = 262144
max_records = 10000
max_state_items = 200
lock_timeout_seconds = 5.0
lock_stale_after_seconds = 900

[policy]
allowed_classifications = ["public", "internal"]
review_warning_days = 14
enforce_ttl_ceiling = true
fail_on_stale_verified = true
require_reciprocal_supersession = true
detect_structured_conflicts = true
'''
