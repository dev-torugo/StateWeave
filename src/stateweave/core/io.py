"""Bounded JSON I/O and atomic filesystem operations."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

from stateweave.core.errors import PathBoundaryError, RecordError


def canonical_json_bytes(payload: Any) -> bytes:
    return (
        json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_relative_path(value: str) -> Path:
    if (
        not value
        or "\x00" in value
        or "\\" in value
        or ":" in value
        or value.startswith("/")
    ):
        raise PathBoundaryError(f"unsafe relative path: {value!r}")
    portable = PurePosixPath(value)
    if portable.is_absolute() or ".." in portable.parts or not portable.parts:
        raise PathBoundaryError(f"unsafe relative path: {value!r}")
    if any(part in {"", "."} for part in portable.parts):
        raise PathBoundaryError(f"unsafe relative path: {value!r}")
    return Path(*portable.parts)


def ensure_within(root: Path, path: Path) -> Path:
    resolved_root = root.resolve()
    resolved_path = path.resolve(strict=False)
    if not resolved_path.is_relative_to(resolved_root):
        raise PathBoundaryError(f"path escapes {resolved_root}: {path}")
    return resolved_path


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number {value}")


def read_json(path: Path, *, max_bytes: int) -> Any:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise RecordError(f"cannot stat {path}: {exc}") from exc
    if size > max_bytes:
        raise RecordError(f"record exceeds {max_bytes} bytes: {path}")
    try:
        return json.loads(
            path.read_text(encoding="utf-8"),
            parse_constant=_reject_json_constant,
        )
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
        raise RecordError(f"invalid JSON record {path}: {exc}") from exc


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    try:
        descriptor = os.open(path, flags)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ensure_within(path.parent.resolve(), path)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def atomic_write_json(path: Path, payload: Any) -> None:
    atomic_write_bytes(path, canonical_json_bytes(payload))
