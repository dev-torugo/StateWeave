"""Project initialization and record mutation."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from stateweave.core.audit import audit_repository
from stateweave.core.backup import project_writer_lock
from stateweave.core.config import (
    CONFIG_FILENAME,
    ProjectConfig,
    load_config,
    render_default_config,
)
from stateweave.core.errors import RecordError
from stateweave.core.io import atomic_write_bytes, atomic_write_json
from stateweave.core.schema import validate_record

ID_TO_KIND = {
    "FCT": "fact",
    "DEC": "decision",
}
SAFE_ID = re.compile(r"^(FCT|DEC)-[A-Za-z0-9][A-Za-z0-9._-]{1,127}$")


def initialize_project(
    destination: str | Path,
    *,
    project_id: str,
    project_name: str,
) -> ProjectConfig:
    """Create a deterministic memory repository in an empty directory."""

    root = Path(destination).resolve(strict=False)
    if root.exists():
        if not root.is_dir():
            raise RecordError(f"project destination is not a directory: {root}")
        if next(root.iterdir(), None) is not None:
            raise RecordError(f"project destination is not empty: {root}")
    root.mkdir(parents=True, exist_ok=True)
    config_path = root / CONFIG_FILENAME
    atomic_write_bytes(
        config_path,
        render_default_config(project_id, project_name).encode("utf-8"),
    )
    config = load_config(config_path)
    config.facts_dir.mkdir(parents=True)
    config.decisions_dir.mkdir(parents=True)
    config.state_file.parent.mkdir(parents=True)
    config.metadata_dir.mkdir(parents=True)
    config.backups_dir.mkdir(parents=True)
    config.migrations_dir.mkdir(parents=True)
    atomic_write_json(
        config.state_file,
        {
            "schema_version": "1.0",
            "kind": "state",
            "id": "STATE-current",
            "updated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "classification": "internal",
            "owner_role": "maintainer",
            "references": [],
            "items": [],
        },
    )
    return config


def record_destination(config: ProjectConfig, identifier: str) -> Path:
    match = SAFE_ID.fullmatch(identifier)
    if match is None:
        raise RecordError(f"unsafe or unsupported record id: {identifier!r}")
    kind = ID_TO_KIND[match.group(1)]
    directory = config.facts_dir if kind == "fact" else config.decisions_dir
    return directory / f"{identifier}.json"


def put_record(
    config: ProjectConfig,
    payload: dict[str, Any],
    *,
    schema_validator: Callable[[dict[str, Any], str, Path], list[str]] | None = None,
    overwrite: bool = False,
) -> Path:
    """Validate and atomically write a fact or decision."""

    destinations = put_records(
        config,
        [payload],
        schema_validator=schema_validator,
        overwrite=overwrite,
    )
    return destinations[0]


def put_records(
    config: ProjectConfig,
    payloads: list[dict[str, Any]],
    *,
    schema_validator: Callable[[dict[str, Any], str, Path], list[str]] | None = None,
    overwrite: bool = False,
) -> list[Path]:
    """Atomically validate a relationship-preserving batch of records."""

    if not payloads:
        raise RecordError("record batch must not be empty")
    prepared: list[tuple[Path, dict[str, Any]]] = []
    seen: set[str] = set()
    for payload in payloads:
        identifier = payload.get("id")
        kind = payload.get("kind")
        if not isinstance(identifier, str):
            raise RecordError("record id must be a string")
        if identifier in seen:
            raise RecordError(f"duplicate record in batch: {identifier}")
        seen.add(identifier)
        destination = record_destination(config, identifier)
        expected_kind = ID_TO_KIND[identifier.split("-", 1)[0]]
        if kind != expected_kind:
            raise RecordError(
                f"record kind {kind!r} does not match id kind {expected_kind!r}"
            )
        if destination.exists() and not overwrite:
            raise RecordError(f"record already exists: {identifier}")
        errors = validate_record(payload, expected_kind, destination)
        if schema_validator is not None:
            errors.extend(schema_validator(payload, expected_kind, destination))
        if errors:
            raise RecordError("; ".join(errors))
        prepared.append((destination, payload))

    with project_writer_lock(config):
        previous = {
            destination: destination.read_bytes() if destination.exists() else None
            for destination, _ in prepared
        }
        try:
            for destination, payload in prepared:
                atomic_write_json(destination, payload)
            report = audit_repository(
                config,
                schema_validator=schema_validator,
                allow_active_writer=True,
            )
            if report.errors:
                raise RecordError(
                    "record batch violates repository invariants: "
                    + "; ".join(report.errors)
                )
        except BaseException as exc:
            rollback_errors: list[str] = []
            for destination, original in previous.items():
                try:
                    if original is None:
                        destination.unlink(missing_ok=True)
                    else:
                        atomic_write_bytes(destination, original)
                except OSError as rollback_exc:
                    rollback_errors.append(f"{destination}: {rollback_exc}")
            if rollback_errors:
                raise RecordError(
                    "record batch failed and rollback was incomplete: "
                    + "; ".join(rollback_errors)
                ) from exc
            raise
    return [destination for destination, _ in prepared]
