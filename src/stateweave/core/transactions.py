"""Durable, hash-bound transaction journals for canonical record batches."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

from stateweave.core.config import ProjectConfig
from stateweave.core.errors import PathBoundaryError, RecordError
from stateweave.core.io import (
    atomic_write_json,
    canonical_json_bytes,
    read_json,
    safe_relative_path,
    sha256_bytes,
)
from stateweave.core.schema import validate_payload

TRANSACTIONS_DIRNAME = "transactions"
JOURNAL_SUFFIX = ".json"
PAYLOAD_SUFFIX = ".payloads"
MAX_JOURNAL_BYTES = 2 * 1024 * 1024
TRANSACTION_ID = re.compile(
    r"^(?:TXN-[0-9]{8}T[0-9]{6}Z-[a-f0-9]{16}|IDEM-[a-f0-9]{64})$"
)
IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")
SHA256 = re.compile(r"^[a-f0-9]{64}$")


@dataclass(frozen=True)
class TransactionEntry:
    transaction_id: str
    path: Path
    payload_dir: Path
    data: dict[str, Any]


@dataclass(frozen=True)
class TransactionStoreReport:
    entries: tuple[TransactionEntry, ...]
    errors: tuple[str, ...]

    @property
    def incomplete(self) -> tuple[TransactionEntry, ...]:
        return tuple(
            entry
            for entry in self.entries
            if entry.data.get("status") in {"preparing", "prepared", "applying"}
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": not self.errors,
            "errors": list(self.errors),
            "transactions": [
                {
                    "id": entry.transaction_id,
                    "status": entry.data["status"],
                    "request_sha256": entry.data["request_sha256"],
                    "idempotency_key_sha256": entry.data["idempotency_key_sha256"],
                    "created_at": entry.data["created_at"],
                    "updated_at": entry.data["updated_at"],
                    "change_count": len(entry.data["changes"]),
                }
                for entry in self.entries
            ],
        }


def transactions_dir(config: ProjectConfig) -> Path:
    return config.metadata_dir / TRANSACTIONS_DIRNAME


def ensure_transactions_dir(config: ProjectConfig) -> Path:
    root = transactions_dir(config)
    if root.is_symlink():
        raise RecordError(f"transaction store may not be a symlink: {root}")
    root.mkdir(parents=True, exist_ok=True)
    if not root.is_dir():
        raise RecordError(f"transaction store must be a directory: {root}")
    return root


def idempotency_digest(key: str) -> str:
    if IDEMPOTENCY_KEY.fullmatch(key) is None:
        raise RecordError(
            "idempotency_key must be 1 to 200 portable identifier characters"
        )
    return sha256_bytes(key.encode("utf-8"))


def transaction_id_for_key(key: str) -> tuple[str, str]:
    digest = idempotency_digest(key)
    return f"IDEM-{digest}", digest


def new_transaction_id() -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"TXN-{timestamp}-{uuid.uuid4().hex[:16]}"


def transaction_request_sha256(
    payloads: Iterable[dict[str, Any]],
    *,
    overwrite: bool,
    expected_sha256_by_id: Mapping[str, str | None] | None,
) -> str:
    request = {
        "payloads": list(payloads),
        "overwrite": overwrite,
        "expected_sha256_by_id": (
            dict(sorted(expected_sha256_by_id.items()))
            if expected_sha256_by_id is not None
            else None
        ),
    }
    return sha256_bytes(canonical_json_bytes(request))


def transaction_path(config: ProjectConfig, transaction_id: str) -> Path:
    if TRANSACTION_ID.fullmatch(transaction_id) is None:
        raise RecordError(f"invalid transaction id: {transaction_id!r}")
    return transactions_dir(config) / f"{transaction_id}{JOURNAL_SUFFIX}"


def transaction_payload_dir(config: ProjectConfig, transaction_id: str) -> Path:
    if TRANSACTION_ID.fullmatch(transaction_id) is None:
        raise RecordError(f"invalid transaction id: {transaction_id!r}")
    return transactions_dir(config) / f"{transaction_id}{PAYLOAD_SUFFIX}"


def _validate_journal(
    payload: dict[str, Any],
    *,
    source: Path,
    expected_id: str,
) -> list[str]:
    errors = validate_payload(payload, "transaction", source)
    if payload.get("id") != expected_id:
        errors.append(f"{source}: transaction id does not match its directory")
    changes = payload.get("changes")
    if not isinstance(changes, list):
        return errors
    indices: set[int] = set()
    record_ids: set[str] = set()
    paths: set[str] = set()
    for item in changes:
        if not isinstance(item, dict):
            continue
        index = item.get("index")
        record_id = item.get("record_id")
        relative = item.get("path")
        if isinstance(index, int):
            if index in indices:
                errors.append(f"{source}: duplicate transaction change index {index}")
            indices.add(index)
        if isinstance(record_id, str):
            if record_id in record_ids:
                errors.append(f"{source}: duplicate transaction record {record_id}")
            record_ids.add(record_id)
        if isinstance(relative, str):
            if relative in paths:
                errors.append(f"{source}: duplicate transaction path {relative}")
            paths.add(relative)
            try:
                safe_relative_path(relative)
            except PathBoundaryError as exc:
                errors.append(f"{source}: unsafe transaction path {relative!r}: {exc}")
    if indices and indices != set(range(len(changes))):
        errors.append(f"{source}: transaction change indices must be contiguous")
    return sorted(set(errors))


def write_transaction_journal(path: Path, payload: dict[str, Any]) -> None:
    errors = _validate_journal(payload, source=path, expected_id=payload.get("id", ""))
    if errors:
        raise RecordError("invalid transaction journal: " + "; ".join(errors))
    atomic_write_json(path, payload)


def load_transaction(
    config: ProjectConfig,
    transaction_id: str,
) -> TransactionEntry:
    directory = transaction_path(config, transaction_id)
    journal_path = directory
    try:
        payload = read_json(journal_path, max_bytes=MAX_JOURNAL_BYTES)
    except RecordError as exc:
        raise RecordError(f"cannot load transaction {transaction_id}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RecordError(f"transaction journal must be an object: {journal_path}")
    errors = _validate_journal(
        payload,
        source=journal_path,
        expected_id=transaction_id,
    )
    if errors:
        raise RecordError("; ".join(errors))
    return TransactionEntry(
        transaction_id,
        journal_path,
        transaction_payload_dir(config, transaction_id),
        payload,
    )


def inspect_transaction_store(
    config: ProjectConfig,
    *,
    active_transaction_id: str | None = None,
) -> TransactionStoreReport:
    root = transactions_dir(config)
    if not root.exists():
        return TransactionStoreReport((), ())
    if root.is_symlink() or not root.is_dir():
        return TransactionStoreReport((), (f"{root}: invalid transaction store",))
    entries: list[TransactionEntry] = []
    errors: list[str] = []
    try:
        children = sorted(root.iterdir(), key=lambda path: path.name)
    except OSError as exc:
        return TransactionStoreReport((), (f"{root}: cannot inspect: {exc}",))
    journal_ids: set[str] = set()
    payload_ids: set[str] = set()
    for child in children:
        if child.is_symlink():
            errors.append(f"{child}: transaction store entry may not be a symlink")
            continue
        if child.is_file() and child.name.endswith(JOURNAL_SUFFIX):
            transaction_id = child.name[: -len(JOURNAL_SUFFIX)]
            if TRANSACTION_ID.fullmatch(transaction_id) is None:
                errors.append(f"{child}: invalid transaction journal name")
                continue
            journal_ids.add(transaction_id)
        elif child.is_dir() and child.name.endswith(PAYLOAD_SUFFIX):
            transaction_id = child.name[: -len(PAYLOAD_SUFFIX)]
            if TRANSACTION_ID.fullmatch(transaction_id) is None:
                errors.append(f"{child}: invalid transaction payload directory name")
                continue
            payload_ids.add(transaction_id)
        else:
            errors.append(f"{child}: unexpected transaction store entry")

    for transaction_id in sorted(journal_ids):
        try:
            entry = load_transaction(config, transaction_id)
        except RecordError as exc:
            errors.append(str(exc))
            continue
        entries.append(entry)
        status = entry.data.get("status")
        if status in {"prepared", "applying"} and transaction_id not in payload_ids:
            errors.append(
                f"incomplete transaction {transaction_id} is missing recovery payloads"
            )
        if (
            status in {"preparing", "prepared", "applying"}
            and transaction_id != active_transaction_id
        ):
            errors.append(
                f"incomplete transaction {transaction_id} requires explicit recovery"
            )
    for transaction_id in sorted(payload_ids - journal_ids):
        errors.append(
            f"orphan transaction payloads {transaction_id} require explicit recovery"
        )
    return TransactionStoreReport(
        tuple(entries),
        tuple(sorted(set(errors))),
    )


def journal_timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def payload_path(directory: Path, index: int, suffix: str) -> Path:
    if suffix not in {"before", "after"}:
        raise ValueError(f"unsupported transaction payload suffix: {suffix}")
    return directory / f"{index:06d}.{suffix}"


def validate_sha256_mapping(
    expected: Mapping[str, str | None] | None,
    record_ids: set[str],
) -> None:
    if expected is None:
        return
    if set(expected) != record_ids:
        raise RecordError(
            "expected_sha256_by_id must contain exactly the batch record ids"
        )
    for identifier, digest in expected.items():
        if digest is not None and SHA256.fullmatch(digest) is None:
            raise RecordError(f"invalid expected SHA-256 for {identifier}")
