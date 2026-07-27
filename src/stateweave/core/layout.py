"""Closed-world discovery for the canonical memory store layout."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from stateweave.core.config import ProjectConfig


@dataclass(frozen=True)
class StoreLayout:
    """Record paths and deterministic layout errors for one project snapshot."""

    record_paths: tuple[tuple[str, Path], ...]
    errors: tuple[str, ...]


def _relative(config: ProjectConfig, path: Path) -> str:
    return path.relative_to(config.root).as_posix()


def inspect_store_layout(config: ProjectConfig) -> StoreLayout:
    """Discover canonical records and reject content hidden from normal audits."""

    records: list[tuple[str, Path]] = []
    errors: list[str] = []
    for kind, directory in (
        ("fact", config.facts_dir),
        ("decision", config.decisions_dir),
    ):
        relative = _relative(config, directory)
        if not directory.exists():
            errors.append(f"{relative}: configured {kind} directory is missing")
            continue
        if directory.is_symlink() or not directory.is_dir():
            errors.append(
                f"{relative}: configured {kind} path must be a real directory"
            )
            continue
        try:
            entries = sorted(directory.iterdir(), key=lambda path: path.name)
        except OSError as exc:
            errors.append(f"{relative}: cannot inspect configured directory: {exc}")
            continue
        for entry in entries:
            entry_relative = _relative(config, entry)
            if entry.is_symlink():
                errors.append(
                    f"{entry_relative}: record area entry may not be a symlink"
                )
            elif entry.is_dir():
                errors.append(f"{entry_relative}: unexpected directory in record area")
            elif not entry.is_file():
                errors.append(f"{entry_relative}: record area entry must be a file")
            elif entry.suffix != ".json":
                errors.append(
                    f"{entry_relative}: unexpected non-JSON record area entry"
                )
            else:
                records.append((kind, entry))

    state = config.state_file
    state_relative = _relative(config, state)
    if not state.exists():
        errors.append(f"{state_relative}: configured state record is missing")
    elif state.is_symlink() or not state.is_file():
        errors.append(f"{state_relative}: state record must be a real file")
    else:
        records.append(("state", state))

    return StoreLayout(
        record_paths=tuple(records),
        errors=tuple(sorted(set(errors))),
    )
