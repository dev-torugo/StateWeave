"""Cross-platform exclusive writer lock with fail-closed stale handling."""

from __future__ import annotations

import json
import os
import socket
import time
import uuid
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Any

from stateweave.core.errors import LockUnavailableError
from stateweave.core.io import atomic_write_json, sha256_bytes, sha256_file

MAX_OWNER_BYTES = 64 * 1024
MISSING_OWNER_FINGERPRINT = sha256_bytes(b"stateweave:missing-lock-owner:v1\n")


@dataclass(frozen=True)
class LockInspection:
    """Immutable evidence used for explicit stale-lock recovery."""

    exists: bool
    stale: bool
    safe_to_recover: bool
    age_seconds: float | None
    owner_sha256: str | None
    owner_token: str | None
    owner: dict[str, Any] | None
    diagnostic: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "exists": self.exists,
            "stale": self.stale,
            "safe_to_recover": self.safe_to_recover,
            "age_seconds": self.age_seconds,
            "owner_sha256": self.owner_sha256,
            "owner_token": self.owner_token,
            "owner": self.owner,
            "diagnostic": self.diagnostic,
        }


def _age_from_timestamp(timestamp: float, now: datetime) -> float:
    observed = datetime.fromtimestamp(timestamp, tz=UTC)
    return max(0.0, (now - observed).total_seconds())


def _owner_age(owner: dict[str, Any], now: datetime) -> float | None:
    acquired_at = owner.get("acquired_at")
    if not isinstance(acquired_at, str):
        return None
    try:
        parsed = datetime.fromisoformat(acquired_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return max(0.0, (now - parsed.astimezone(UTC)).total_seconds())


def _inspect_lock_path(
    lock_dir: Path,
    *,
    stale_after_seconds: int,
    now: datetime,
) -> LockInspection:
    if lock_dir.is_symlink():
        return LockInspection(
            exists=True,
            stale=False,
            safe_to_recover=False,
            age_seconds=None,
            owner_sha256=None,
            owner_token=None,
            owner=None,
            diagnostic="writer lock path may not be a symlink",
        )
    if not lock_dir.exists():
        return LockInspection(
            exists=False,
            stale=False,
            safe_to_recover=False,
            age_seconds=None,
            owner_sha256=None,
            owner_token=None,
            owner=None,
            diagnostic="writer lock is absent",
        )
    if not lock_dir.is_dir():
        return LockInspection(
            exists=True,
            stale=False,
            safe_to_recover=False,
            age_seconds=None,
            owner_sha256=None,
            owner_token=None,
            owner=None,
            diagnostic="writer lock path is not a real directory",
        )
    try:
        entries = sorted(item.name for item in lock_dir.iterdir())
        lock_age = _age_from_timestamp(lock_dir.stat().st_mtime, now)
    except OSError as exc:
        return LockInspection(
            exists=True,
            stale=False,
            safe_to_recover=False,
            age_seconds=None,
            owner_sha256=None,
            owner_token=None,
            owner=None,
            diagnostic=f"cannot inspect writer lock: {exc}",
        )
    unexpected = [name for name in entries if name != "owner.json"]
    if unexpected:
        return LockInspection(
            exists=True,
            stale=lock_age > stale_after_seconds,
            safe_to_recover=False,
            age_seconds=lock_age,
            owner_sha256=None,
            owner_token=None,
            owner=None,
            diagnostic=f"writer lock has unexpected entries: {unexpected}",
        )

    owner_file = lock_dir / "owner.json"
    if owner_file.is_symlink():
        return LockInspection(
            exists=True,
            stale=lock_age > stale_after_seconds,
            safe_to_recover=False,
            age_seconds=lock_age,
            owner_sha256=None,
            owner_token=None,
            owner=None,
            diagnostic="writer lock owner may not be a symlink",
        )
    if not owner_file.exists():
        return LockInspection(
            exists=True,
            stale=lock_age > stale_after_seconds,
            safe_to_recover=True,
            age_seconds=lock_age,
            owner_sha256=MISSING_OWNER_FINGERPRINT,
            owner_token=None,
            owner=None,
            diagnostic="writer lock owner is missing",
        )
    if not owner_file.is_file():
        return LockInspection(
            exists=True,
            stale=lock_age > stale_after_seconds,
            safe_to_recover=False,
            age_seconds=lock_age,
            owner_sha256=None,
            owner_token=None,
            owner=None,
            diagnostic="writer lock owner is not a real file",
        )
    owner_sha256: str | None = None
    try:
        owner_size = owner_file.stat().st_size
        owner_sha256 = sha256_file(owner_file)
        if owner_size > MAX_OWNER_BYTES:
            return LockInspection(
                exists=True,
                stale=lock_age > stale_after_seconds,
                safe_to_recover=False,
                age_seconds=lock_age,
                owner_sha256=owner_sha256,
                owner_token=None,
                owner=None,
                diagnostic="writer lock owner exceeds the size limit",
            )
        owner_bytes = owner_file.read_bytes()
        owner_payload = json.loads(owner_bytes.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return LockInspection(
            exists=True,
            stale=lock_age > stale_after_seconds,
            safe_to_recover=owner_sha256 is not None,
            age_seconds=lock_age,
            owner_sha256=owner_sha256,
            owner_token=None,
            owner=None,
            diagnostic=f"writer lock owner is invalid: {type(exc).__name__}",
        )
    if not isinstance(owner_payload, dict):
        return LockInspection(
            exists=True,
            stale=lock_age > stale_after_seconds,
            safe_to_recover=True,
            age_seconds=lock_age,
            owner_sha256=owner_sha256,
            owner_token=None,
            owner=None,
            diagnostic="writer lock owner must be an object",
        )
    owner_age = _owner_age(owner_payload, now)
    age = lock_age if owner_age is None else owner_age
    token = owner_payload.get("token")
    owner_token = token if isinstance(token, str) and token else None
    return LockInspection(
        exists=True,
        stale=age > stale_after_seconds,
        safe_to_recover=True,
        age_seconds=age,
        owner_sha256=owner_sha256,
        owner_token=owner_token,
        owner=owner_payload,
        diagnostic="writer lock owner inspected",
    )


def inspect_writer_lock(
    metadata_dir: Path,
    *,
    stale_after_seconds: int,
    now: datetime | None = None,
) -> LockInspection:
    """Inspect writer ownership without mutating or transferring the lock."""

    current = now or datetime.now(UTC)
    if current.tzinfo is None or current.utcoffset() is None:
        raise LockUnavailableError("lock inspection time must be timezone-aware")
    return _inspect_lock_path(
        metadata_dir / "writer.lock",
        stale_after_seconds=stale_after_seconds,
        now=current.astimezone(UTC),
    )


def recover_stale_writer_lock(
    metadata_dir: Path,
    *,
    stale_after_seconds: int,
    expected_owner_sha256: str,
    expected_token: str | None,
) -> LockInspection:
    """Quarantine and remove one explicitly confirmed stale writer lock."""

    inspection = inspect_writer_lock(
        metadata_dir,
        stale_after_seconds=stale_after_seconds,
    )
    if not inspection.exists:
        raise LockUnavailableError("writer lock is already absent")
    if not inspection.safe_to_recover:
        raise LockUnavailableError(
            f"writer lock is not safely recoverable: {inspection.diagnostic}"
        )
    if not inspection.stale:
        raise LockUnavailableError("writer lock is not stale by project policy")
    if inspection.owner_sha256 != expected_owner_sha256:
        raise LockUnavailableError("writer lock owner fingerprint changed")
    if inspection.owner_token != expected_token:
        raise LockUnavailableError("writer lock owner token does not match")

    lock_dir = metadata_dir / "writer.lock"
    quarantine = metadata_dir / f".writer.lock.recovery-{uuid.uuid4().hex}"
    try:
        os.replace(lock_dir, quarantine)
    except OSError as exc:
        raise LockUnavailableError(
            f"cannot quarantine stale writer lock: {exc}"
        ) from exc
    quarantined = _inspect_lock_path(
        quarantine,
        stale_after_seconds=stale_after_seconds,
        now=datetime.now(UTC),
    )
    if (
        not quarantined.safe_to_recover
        or quarantined.owner_sha256 != expected_owner_sha256
        or quarantined.owner_token != expected_token
    ):
        raise LockUnavailableError(
            f"quarantined writer evidence changed; preserved at {quarantine}"
        )
    try:
        owner_file = quarantine / "owner.json"
        if owner_file.exists():
            owner_file.unlink()
        quarantine.rmdir()
    except OSError as exc:
        raise LockUnavailableError(
            f"stale writer lock was quarantined but not removed: {quarantine}: {exc}"
        ) from exc
    return inspection


class WriterLock(AbstractContextManager["WriterLock"]):
    """A lock based on atomic directory creation.

    A stale lock is reported but never stolen automatically. This avoids
    transferring ownership merely because a clock threshold elapsed.
    """

    def __init__(
        self,
        metadata_dir: Path,
        *,
        timeout_seconds: float,
        stale_after_seconds: int,
        poll_interval: float = 0.05,
    ) -> None:
        self.lock_dir = metadata_dir / "writer.lock"
        self.owner_file = self.lock_dir / "owner.json"
        self.timeout_seconds = timeout_seconds
        self.stale_after_seconds = stale_after_seconds
        self.poll_interval = poll_interval
        self.token = uuid.uuid4().hex
        self.acquired = False

    def _owner(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "token": self.token,
            "pid": os.getpid(),
            "hostname": socket.gethostname(),
            "acquired_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        }

    def _age_seconds(self) -> float | None:
        inspection = inspect_writer_lock(
            self.lock_dir.parent,
            stale_after_seconds=self.stale_after_seconds,
        )
        return inspection.age_seconds

    def acquire(self) -> "WriterLock":
        self.lock_dir.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self.timeout_seconds
        while True:
            try:
                self.lock_dir.mkdir()
                atomic_write_json(self.owner_file, self._owner())
                self.acquired = True
                return self
            except FileExistsError:
                age = self._age_seconds()
                stale = age is not None and age > self.stale_after_seconds
                if time.monotonic() >= deadline:
                    suffix = (
                        f"; existing lock is stale by policy ({age:.1f}s)"
                        if stale
                        else ""
                    )
                    raise LockUnavailableError(
                        f"writer lock unavailable: {self.lock_dir}{suffix}",
                        stale=stale,
                    )
                time.sleep(self.poll_interval)
            except BaseException:
                if self.lock_dir.is_dir() and not self.owner_file.exists():
                    try:
                        self.lock_dir.rmdir()
                    except OSError:
                        pass
                raise

    def release(self) -> None:
        if not self.acquired:
            return
        try:
            owner = json.loads(self.owner_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise LockUnavailableError(
                f"cannot verify writer lock ownership: {self.lock_dir}"
            ) from exc
        if owner.get("token") != self.token:
            raise LockUnavailableError(
                f"writer lock ownership changed: {self.lock_dir}"
            )
        self.owner_file.unlink()
        self.lock_dir.rmdir()
        self.acquired = False

    def __enter__(self) -> "WriterLock":
        return self.acquire()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.release()
