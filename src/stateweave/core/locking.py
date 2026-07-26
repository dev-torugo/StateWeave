"""Cross-platform exclusive writer lock with fail-closed stale handling."""

from __future__ import annotations

import json
import os
import socket
import time
import uuid
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType

from stateweave.core.errors import LockUnavailableError
from stateweave.core.io import atomic_write_json


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
        try:
            payload = json.loads(self.owner_file.read_text(encoding="utf-8"))
            acquired_at = payload.get("acquired_at")
            if not isinstance(acquired_at, str):
                return None
            parsed = datetime.fromisoformat(acquired_at.replace("Z", "+00:00"))
            return max(0.0, (datetime.now(UTC) - parsed).total_seconds())
        except (OSError, ValueError, json.JSONDecodeError):
            return None

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
