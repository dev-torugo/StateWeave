"""Project initialization and record mutation."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Mapping

from stateweave.core.audit import audit_repository
from stateweave.core.backup import project_writer_lock
from stateweave.core.config import (
    CONFIG_FILENAME,
    ProjectConfig,
    load_config,
    render_default_config,
)
from stateweave.core.errors import RecordError
from stateweave.core.io import (
    atomic_write_bytes,
    atomic_write_json,
    canonical_json_bytes,
    safe_relative_path,
    sha256_bytes,
    sha256_file,
)
from stateweave.core.schema import validate_record
from stateweave.core.transactions import (
    ensure_transactions_dir,
    inspect_transaction_store,
    journal_timestamp,
    load_transaction,
    new_transaction_id,
    payload_path,
    transaction_id_for_key,
    transaction_path,
    transaction_payload_dir,
    transaction_request_sha256,
    validate_sha256_mapping,
    write_transaction_journal,
)

ID_TO_KIND = {
    "FCT": "fact",
    "DEC": "decision",
}
SAFE_ID = re.compile(r"^(FCT|DEC)-[A-Za-z0-9][A-Za-z0-9._-]{1,127}$")
STATE_ID = "STATE-current"


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
            raise RecordError(
                f"project destination is not empty: {root}; "
                "use `stateweave adopt` for an existing project"
            )
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
    ensure_transactions_dir(config)
    config.backups_dir.mkdir(parents=True)
    config.migrations_dir.mkdir(parents=True)
    config.extensions_dir.mkdir(parents=True)
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
    if identifier == STATE_ID:
        return config.state_file
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
    expected_sha256: str | None = None,
    idempotency_key: str | None = None,
) -> Path:
    """Validate and atomically write a fact or decision."""

    identifier = payload.get("id")
    expected_by_id: dict[str, str | None] | None = None
    if expected_sha256 is not None:
        if not isinstance(identifier, str):
            raise RecordError("record id must be a string")
        expected_by_id = {identifier: expected_sha256}
    destinations = put_records(
        config,
        [payload],
        schema_validator=schema_validator,
        overwrite=overwrite,
        expected_sha256_by_id=expected_by_id,
        idempotency_key=idempotency_key,
    )
    return destinations[0]


def put_records(
    config: ProjectConfig,
    payloads: list[dict[str, Any]],
    *,
    schema_validator: Callable[[dict[str, Any], str, Path], list[str]] | None = None,
    overwrite: bool = False,
    expected_sha256_by_id: Mapping[str, str | None] | None = None,
    idempotency_key: str | None = None,
) -> list[Path]:
    """Durably validate and mutate a relationship-preserving record batch."""

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
        expected_kind = (
            "state"
            if identifier == STATE_ID
            else ID_TO_KIND[identifier.split("-", 1)[0]]
        )
        if kind != expected_kind:
            raise RecordError(
                f"record kind {kind!r} does not match id kind {expected_kind!r}"
            )
        errors = validate_record(payload, expected_kind, destination)
        if schema_validator is not None:
            errors.extend(schema_validator(payload, expected_kind, destination))
        if errors:
            raise RecordError("; ".join(errors))
        prepared.append((destination, payload))

    validate_sha256_mapping(expected_sha256_by_id, seen)
    request_sha256 = transaction_request_sha256(
        payloads,
        overwrite=overwrite,
        expected_sha256_by_id=expected_sha256_by_id,
    )
    if idempotency_key is None:
        transaction_id = new_transaction_id()
        key_sha256 = None
    else:
        transaction_id, key_sha256 = transaction_id_for_key(idempotency_key)

    with project_writer_lock(config):
        journal_path = transaction_path(config, transaction_id)
        transaction_directory = transaction_payload_dir(config, transaction_id)
        if journal_path.exists() or transaction_directory.exists():
            replay = _idempotent_replay(
                config,
                transaction_id=transaction_id,
                request_sha256=request_sha256,
            )
            if replay is not None:
                return replay
            raise RecordError(f"transaction id already exists: {transaction_id}")
        transaction_report = inspect_transaction_store(config)
        if transaction_report.errors:
            raise RecordError(
                "transaction store requires recovery: "
                + "; ".join(transaction_report.errors)
            )

        previous: dict[Path, bytes | None] = {}
        changes: list[dict[str, Any]] = []
        after_payloads: list[bytes] = []
        for index, (destination, payload) in enumerate(prepared):
            identifier = payload["id"]
            if destination.is_symlink():
                raise RecordError(
                    f"record destination may not be a symlink: {identifier}"
                )
            exists = destination.exists()
            if exists and not overwrite:
                raise RecordError(f"record already exists: {identifier}")
            if exists:
                size = destination.stat().st_size
                if size > config.limits.max_record_bytes:
                    raise RecordError(
                        f"existing record exceeds configured byte limit: {identifier}"
                    )
                before = destination.read_bytes()
                before_sha256 = sha256_bytes(before)
            else:
                before = None
                before_sha256 = None
            if expected_sha256_by_id is not None:
                expected = expected_sha256_by_id[identifier]
                if before_sha256 != expected:
                    raise RecordError(
                        f"record revision precondition failed for {identifier}: "
                        f"expected {expected!r}, observed {before_sha256!r}"
                    )
            after = canonical_json_bytes(payload)
            previous[destination] = before
            after_payloads.append(after)
            changes.append(
                {
                    "index": index,
                    "record_id": identifier,
                    "path": destination.relative_to(config.root).as_posix(),
                    "before_sha256": before_sha256,
                    "after_sha256": sha256_bytes(after),
                }
            )

        ensure_transactions_dir(config)
        created_at = journal_timestamp()
        journal: dict[str, Any] = {
            "schema_version": 1,
            "kind": "transaction",
            "id": transaction_id,
            "status": "preparing",
            "request_sha256": request_sha256,
            "idempotency_key_sha256": key_sha256,
            "created_at": created_at,
            "updated_at": created_at,
            "error_type": None,
            "recovered_at": None,
            "changes": changes,
        }
        try:
            write_transaction_journal(journal_path, journal)
            transaction_directory.mkdir()
            for index, after in enumerate(after_payloads):
                atomic_write_bytes(
                    payload_path(transaction_directory, index, "after"),
                    after,
                )
                before = previous[prepared[index][0]]
                if before is not None:
                    atomic_write_bytes(
                        payload_path(transaction_directory, index, "before"),
                        before,
                    )
            journal["status"] = "prepared"
            journal["updated_at"] = journal_timestamp()
            write_transaction_journal(journal_path, journal)
            journal["status"] = "applying"
            journal["updated_at"] = journal_timestamp()
            write_transaction_journal(journal_path, journal)
            for (destination, _), after in zip(prepared, after_payloads):
                atomic_write_bytes(destination, after)
            report = audit_repository(
                config,
                schema_validator=schema_validator,
                allow_active_writer=True,
                active_transaction_id=transaction_id,
            )
            if report.errors:
                raise RecordError(
                    "record batch violates repository invariants: "
                    + "; ".join(report.errors)
                )
            journal["status"] = "committed"
            journal["updated_at"] = journal_timestamp()
            write_transaction_journal(journal_path, journal)
            try:
                _cleanup_transaction_payloads(transaction_directory, len(changes))
            except OSError:
                pass
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
            journal["status"] = "rolled_back"
            journal["updated_at"] = journal_timestamp()
            journal["error_type"] = type(exc).__name__
            try:
                write_transaction_journal(journal_path, journal)
            except BaseException as journal_exc:
                raise RecordError(
                    f"record batch failed and transaction journal could not close: "
                    f"{journal_exc}"
                ) from exc
            try:
                _cleanup_transaction_payloads(transaction_directory, len(changes))
            except OSError:
                pass
            raise
    return [destination for destination, _ in prepared]


def _transaction_destination(
    config: ProjectConfig,
    change: dict[str, Any],
) -> Path:
    identifier = change["record_id"]
    destination = record_destination(config, identifier)
    if destination.is_symlink():
        raise RecordError(f"transaction destination is a symlink: {identifier}")
    declared = (config.root / safe_relative_path(change["path"])).resolve(strict=False)
    if declared != destination.resolve(strict=False):
        raise RecordError(f"transaction destination mismatch for {identifier}")
    return destination


def _idempotent_replay(
    config: ProjectConfig,
    *,
    transaction_id: str,
    request_sha256: str,
) -> list[Path] | None:
    entry = load_transaction(config, transaction_id)
    if entry.data["request_sha256"] != request_sha256:
        raise RecordError("idempotency key was reused for a different request")
    if entry.data["status"] != "committed":
        return None
    destinations: list[Path] = []
    for change in entry.data["changes"]:
        destination = _transaction_destination(config, change)
        if not destination.is_file() or destination.is_symlink():
            raise RecordError(
                f"committed idempotency result is missing: {change['record_id']}"
            )
        if sha256_file(destination) != change["after_sha256"]:
            raise RecordError(
                f"committed idempotency result drifted: {change['record_id']}"
            )
        destinations.append(destination)
    return destinations


def _cleanup_transaction_payloads(directory: Path, count: int) -> None:
    for index in range(count):
        payload_path(directory, index, "before").unlink(missing_ok=True)
        payload_path(directory, index, "after").unlink(missing_ok=True)
    if directory.exists():
        directory.rmdir()


def recover_record_transaction(
    config: ProjectConfig,
    transaction_id: str,
    *,
    expected_request_sha256: str,
) -> dict[str, Any]:
    """Rollback one interrupted transaction after exact evidence confirmation."""

    with project_writer_lock(config):
        entry = load_transaction(config, transaction_id)
        journal = entry.data
        if journal["request_sha256"] != expected_request_sha256:
            raise RecordError("transaction request fingerprint changed")
        if journal["status"] not in {"preparing", "prepared", "applying"}:
            raise RecordError(
                f"transaction {transaction_id} is not incomplete: {journal['status']}"
            )
        preparing = journal["status"] == "preparing"
        for change in journal["changes"]:
            destination = _transaction_destination(config, change)
            observed = sha256_file(destination) if destination.is_file() else None
            allowed = (
                {change["before_sha256"]}
                if preparing
                else {change["before_sha256"], change["after_sha256"]}
            )
            if observed not in allowed:
                raise RecordError(
                    f"transaction recovery found external drift: {change['record_id']}"
                )
            before_sha256 = change["before_sha256"]
            if before_sha256 is None or preparing:
                continue
            before_path = payload_path(
                entry.payload_dir,
                change["index"],
                "before",
            )
            if not before_path.is_file() or sha256_file(before_path) != before_sha256:
                raise RecordError(
                    f"transaction recovery evidence is invalid: {change['record_id']}"
                )
        if not preparing:
            for change in journal["changes"]:
                destination = _transaction_destination(config, change)
                if change["before_sha256"] is None:
                    destination.unlink(missing_ok=True)
                else:
                    before = payload_path(
                        entry.payload_dir,
                        change["index"],
                        "before",
                    )
                    atomic_write_bytes(destination, before.read_bytes())
        report = audit_repository(
            config,
            allow_active_writer=True,
            active_transaction_id=transaction_id,
        )
        if report.errors:
            raise RecordError(
                "transaction rollback violates repository invariants: "
                + "; ".join(report.errors)
            )
        journal["status"] = "rolled_back"
        journal["updated_at"] = journal_timestamp()
        journal["error_type"] = "RecoveredInterruptedTransaction"
        journal["recovered_at"] = journal_timestamp()
        write_transaction_journal(entry.path, journal)
        try:
            _cleanup_transaction_payloads(
                entry.payload_dir,
                len(journal["changes"]),
            )
        except OSError:
            pass
        return journal
