"""Integrity-checked backups and path-safe restore."""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import tempfile
import uuid
import zipfile
from contextlib import nullcontext
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ContextManager

from stateweave.core.config import ProjectConfig, load_config
from stateweave.core.errors import BackupError, PathBoundaryError, StateWeaveError
from stateweave.core.io import (
    atomic_write_bytes,
    canonical_json_bytes,
    safe_relative_path,
    sha256_bytes,
)
from stateweave.core.locking import WriterLock
from stateweave.core.layout import inspect_store_layout

MANIFEST_NAME = "STATEWEAVE-BACKUP.json"
MAX_BACKUP_ENTRY_BYTES = 16 * 1024 * 1024
MAX_BACKUP_TOTAL_BYTES = 128 * 1024 * 1024
LABEL_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")


def project_writer_lock(config: ProjectConfig) -> WriterLock:
    return WriterLock(
        config.metadata_dir,
        timeout_seconds=config.limits.lock_timeout_seconds,
        stale_after_seconds=config.limits.lock_stale_after_seconds,
    )


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number {value}")


def _project_files(config: ProjectConfig) -> list[Path]:
    layout = inspect_store_layout(config)
    if layout.errors:
        raise BackupError("invalid memory store layout: " + "; ".join(layout.errors))
    files = [
        config.source,
        *(path for _, path in layout.record_paths),
        *_extension_files(config),
    ]
    unique: dict[str, Path] = {}
    for path in files:
        if path.is_symlink():
            raise BackupError(f"backup source may not be a symlink: {path}")
        if not path.is_file():
            raise BackupError(f"backup source is not a file: {path}")
        relative = path.resolve().relative_to(config.root.resolve()).as_posix()
        unique[relative] = path
    return [unique[key] for key in sorted(unique)]


def _extension_files(config: ProjectConfig) -> list[Path]:
    """Discover opaque extension artifacts without following symlinks."""

    root = config.extensions_dir
    if not root.exists():
        return []
    if root.is_symlink() or not root.is_dir():
        raise BackupError(f"extensions path must be a real directory: {root}")
    files: list[Path] = []
    pending = [root]
    while pending:
        directory = pending.pop()
        try:
            entries = sorted(directory.iterdir(), key=lambda path: path.name)
        except OSError as exc:
            raise BackupError(f"cannot inspect extension directory {directory}: {exc}")
        for entry in entries:
            if entry.is_symlink():
                raise BackupError(f"extension artifact may not be a symlink: {entry}")
            if entry.is_dir():
                pending.append(entry)
            elif entry.is_file():
                files.append(entry)
            else:
                raise BackupError(f"extension artifact must be a file: {entry}")
    return files


def _manifest(
    config: ProjectConfig, files: list[Path]
) -> tuple[dict[str, Any], dict[str, bytes]]:
    members: dict[str, bytes] = {}
    records: list[dict[str, Any]] = []
    total = 0
    for path in files:
        relative = path.resolve().relative_to(config.root.resolve()).as_posix()
        payload = path.read_bytes()
        if len(payload) > MAX_BACKUP_ENTRY_BYTES:
            raise BackupError(f"backup member too large: {relative}")
        total += len(payload)
        if total > MAX_BACKUP_TOTAL_BYTES:
            raise BackupError("backup exceeds uncompressed size limit")
        members[relative] = payload
        records.append(
            {
                "path": relative,
                "size": len(payload),
                "sha256": sha256_bytes(payload),
            }
        )
    created_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    return (
        {
            "schema_version": 1,
            "created_at": created_at,
            "project_id": config.project_id,
            "files": records,
        },
        members,
    )


def create_backup(
    config: ProjectConfig,
    *,
    label: str = "manual",
    acquire_lock: bool = True,
) -> Path:
    """Create an atomic ZIP backup with a content-hash manifest."""

    if LABEL_PATTERN.fullmatch(label) is None:
        raise BackupError("backup label must be lowercase alphanumeric with hyphens")
    lock: ContextManager[object] = (
        project_writer_lock(config) if acquire_lock else nullcontext()
    )
    with lock:
        files = _project_files(config)
        manifest, members = _manifest(config, files)
        config.backups_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        destination = (
            config.backups_dir / f"{timestamp}-{label}-{uuid.uuid4().hex[:8]}.zip"
        )
        descriptor, temporary_name = tempfile.mkstemp(
            dir=config.backups_dir,
            prefix=".backup-",
            suffix=".tmp",
        )
        os.close(descriptor)
        temporary = Path(temporary_name)
        try:
            with zipfile.ZipFile(
                temporary,
                mode="w",
                compression=zipfile.ZIP_DEFLATED,
                compresslevel=9,
            ) as archive:
                for relative, payload in sorted(members.items()):
                    archive.writestr(relative, payload)
                archive.writestr(MANIFEST_NAME, canonical_json_bytes(manifest))
            os.replace(temporary, destination)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
        return destination


def _read_verified_backup(
    backup_path: Path,
) -> tuple[dict[str, Any], dict[str, bytes]]:
    try:
        archive = zipfile.ZipFile(backup_path, mode="r")
    except (OSError, zipfile.BadZipFile) as exc:
        raise BackupError(f"invalid backup archive {backup_path}: {exc}") from exc
    with archive:
        infos = archive.infolist()
        names = [info.filename for info in infos]
        if len(names) != len(set(names)):
            raise BackupError("backup contains duplicate member names")
        if MANIFEST_NAME not in names:
            raise BackupError(f"backup is missing {MANIFEST_NAME}")
        manifest_info = archive.getinfo(MANIFEST_NAME)
        if manifest_info.file_size > MAX_BACKUP_ENTRY_BYTES:
            raise BackupError("backup manifest exceeds size limit")
        try:
            manifest_payload = archive.read(MANIFEST_NAME)
            manifest = json.loads(
                manifest_payload.decode("utf-8"),
                parse_constant=_reject_json_constant,
            )
        except (KeyError, UnicodeError, ValueError, RecursionError) as exc:
            raise BackupError(f"invalid backup manifest: {exc}") from exc
        if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
            raise BackupError("unsupported backup manifest schema")
        file_records = manifest.get("files")
        if not isinstance(file_records, list):
            raise BackupError("backup manifest files must be an array")
        expected_names = {MANIFEST_NAME}
        payloads: dict[str, bytes] = {}
        total = 0
        for item in file_records:
            if not isinstance(item, dict):
                raise BackupError("backup manifest file entry must be an object")
            relative_value = item.get("path")
            if not isinstance(relative_value, str):
                raise BackupError("backup manifest path must be a string")
            try:
                relative = safe_relative_path(relative_value).as_posix()
            except PathBoundaryError as exc:
                raise BackupError(
                    f"unsafe backup member path: {relative_value!r}"
                ) from exc
            if relative == MANIFEST_NAME or relative in expected_names:
                raise BackupError(f"duplicate or reserved backup path: {relative}")
            expected_names.add(relative)
            try:
                info = archive.getinfo(relative)
            except KeyError as exc:
                raise BackupError(f"backup member is missing: {relative}") from exc
            mode = (info.external_attr >> 16) & 0o170000
            if mode == stat.S_IFLNK or info.is_dir():
                raise BackupError(f"backup member must be a regular file: {relative}")
            if info.file_size > MAX_BACKUP_ENTRY_BYTES:
                raise BackupError(f"backup member too large: {relative}")
            total += info.file_size
            if total > MAX_BACKUP_TOTAL_BYTES:
                raise BackupError("backup exceeds uncompressed size limit")
            payload = archive.read(info)
            if item.get("size") != len(payload):
                raise BackupError(f"backup size mismatch: {relative}")
            if item.get("sha256") != sha256_bytes(payload):
                raise BackupError(f"backup hash mismatch: {relative}")
            payloads[relative] = payload
        if set(names) != expected_names:
            unexpected = sorted(set(names) - expected_names)
            raise BackupError(f"backup contains unexpected members: {unexpected}")
        return manifest, payloads


def _destination_is_empty(destination: Path) -> bool:
    return not destination.exists() or (
        destination.is_dir() and next(destination.iterdir(), None) is None
    )


def _prepare_restored_project(
    staging: Path,
    manifest: dict[str, Any],
) -> None:
    """Validate restored configuration and materialize canonical empty dirs."""

    try:
        config = load_config(staging)
    except StateWeaveError as exc:
        raise BackupError(f"restored project configuration is invalid: {exc}") from exc
    if manifest.get("project_id") != config.project_id:
        raise BackupError(
            "backup manifest project_id does not match restored configuration"
        )
    directories = (
        config.facts_dir,
        config.decisions_dir,
        config.state_file.parent,
        config.metadata_dir,
        config.metadata_dir / "transactions",
        config.backups_dir,
        config.migrations_dir,
        config.extensions_dir,
    )
    try:
        for directory in directories:
            directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise BackupError(f"cannot materialize restored project layout: {exc}") from exc


def restore_backup(
    backup_path: str | Path,
    destination: str | Path,
    *,
    require_empty: bool = True,
) -> dict[str, Any]:
    """Restore verified members without using `extractall`."""

    source = Path(backup_path).resolve()
    target = Path(destination).resolve(strict=False)
    if target.parent == target:
        raise BackupError("restore destination may not be a filesystem root")
    if target.exists() and not target.is_dir():
        raise BackupError(f"restore destination is not a directory: {target}")
    if require_empty and not _destination_is_empty(target):
        raise BackupError(f"restore destination is not empty: {target}")
    manifest, members = _read_verified_backup(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.parent / f".{target.name}.restore-{uuid.uuid4().hex}"
    staging.mkdir()
    target_existed = target.exists()
    try:
        for relative, payload in sorted(members.items()):
            path = (staging / safe_relative_path(relative)).resolve(strict=False)
            if not path.is_relative_to(staging):
                raise BackupError(f"restore path escapes destination: {relative}")
            atomic_write_bytes(path, payload)
        _prepare_restored_project(staging, manifest)
        if target_existed:
            target.rmdir()
        try:
            os.replace(staging, target)
        except BaseException:
            if target_existed and not target.exists():
                target.mkdir()
            raise
    except BaseException:
        if staging.exists():
            shutil.rmtree(staging)
        raise
    return manifest


def restore_backup_over_project(
    backup_path: Path,
    config: ProjectConfig,
) -> dict[str, Any]:
    """Internal rollback helper that overwrites only verified backup members."""

    manifest, members = _read_verified_backup(backup_path)
    root = config.root.resolve()
    for relative, payload in sorted(members.items()):
        path = (root / safe_relative_path(relative)).resolve(strict=False)
        if not path.is_relative_to(root):
            raise BackupError(f"rollback path escapes project: {relative}")
        atomic_write_bytes(path, payload)
    return manifest
