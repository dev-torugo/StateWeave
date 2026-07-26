#!/usr/bin/env python3
"""Validate extraction, neutrality, and provenance contracts."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from stateweave.contracts import check_module_schemas
from stateweave.core.schema import check_packaged_schemas

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "src/stateweave/core"
MANIFEST = ROOT / "docs/provenance/TRANSFORMATION-MANIFEST.json"
CI_WORKFLOW = ROOT / ".github/workflows/ci.yml"

FORBIDDEN_CORE_PATTERNS = {
    "source project name": re.compile(r"\bGeoCapta\b", re.IGNORECASE),
    "fixed founder authority": re.compile(r"\bfounder\b", re.IGNORECASE),
    "Codex runtime": re.compile(r"\bCodex\b", re.IGNORECASE),
    "concrete GPT model": re.compile(r"\bgpt-[a-z0-9.-]+\b", re.IGNORECASE),
    "concrete Spark model": re.compile(r"\bSpark\b"),
    "concrete Sol model": re.compile(r"\bSol\b"),
    "source agents path": re.compile(r"(?:^|[\"'])agents/"),
    "absolute user path": re.compile(r"/Users/[^/\s]+/"),
}

HIGH_CONFIDENCE_SECRET_PATTERNS = {
    "OpenAI-style key": re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b"),
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "AWS access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
}


def _text_files(root: Path) -> list[Path]:
    excluded = {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
        "build",
        "dist",
    }
    return [
        path
        for path in sorted(root.rglob("*"))
        if path.is_file()
        and not excluded.intersection(path.parts)
        and path.suffix
        in {
            "",
            ".json",
            ".md",
            ".py",
            ".toml",
            ".txt",
            ".yml",
            ".yaml",
        }
    ]


def check_core_neutrality() -> list[str]:
    errors: list[str] = []
    for path in _text_files(CORE):
        text = path.read_text(encoding="utf-8")
        for label, pattern in FORBIDDEN_CORE_PATTERNS.items():
            if pattern.search(text):
                errors.append(f"{path.relative_to(ROOT)}: forbidden {label} reference")
    return errors


def check_sensitive_content() -> list[str]:
    errors: list[str] = []
    for path in _text_files(ROOT):
        text = path.read_text(encoding="utf-8")
        for label, pattern in HIGH_CONFIDENCE_SECRET_PATTERNS.items():
            if pattern.search(text):
                errors.append(f"{path.relative_to(ROOT)}: possible {label}")
        if "@" in text and path.name not in {"check_contract.py"}:
            email = re.search(
                r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
                text,
            )
            if email:
                errors.append(f"{path.relative_to(ROOT)}: email-like personal data")
        if path.name != "check_contract.py" and re.search(
            r"/Users/[^/\s]+/",
            text,
        ):
            errors.append(
                f"{path.relative_to(ROOT)}: absolute user path is not exportable"
            )
    return errors


def check_provenance() -> list[str]:
    errors: list[str] = []
    try:
        payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"invalid provenance manifest: {exc}"]
    if payload.get("schema_version") != "1.0":
        errors.append("provenance manifest: unsupported schema_version")
    source = payload.get("source_repository")
    if (
        not isinstance(source, dict)
        or source.get("absolute_path_exported") is not False
    ):
        errors.append("provenance manifest: absolute source path policy is missing")
    files = payload.get("files")
    if not isinstance(files, list) or not files:
        return errors + ["provenance manifest: files must be a non-empty array"]
    seen_sources: set[str] = set()
    for index, item in enumerate(files):
        if not isinstance(item, dict):
            errors.append(f"provenance manifest: files[{index}] must be an object")
            continue
        source_path = item.get("source")
        if not isinstance(source_path, str) or not source_path:
            errors.append(f"provenance manifest: files[{index}] source is missing")
        elif source_path in seen_sources:
            errors.append(f"provenance manifest: duplicate source {source_path}")
        else:
            seen_sources.add(source_path)
        digest = item.get("source_worktree_sha256")
        if not isinstance(digest, str) or re.fullmatch(r"[a-f0-9]{64}", digest) is None:
            errors.append(
                f"provenance manifest: files[{index}] has invalid source hash"
            )
        destinations = item.get("destinations")
        if not isinstance(destinations, list) or not destinations:
            errors.append(
                f"provenance manifest: files[{index}] destinations are missing"
            )
            continue
        for destination in destinations:
            if not isinstance(destination, str):
                errors.append(
                    f"provenance manifest: files[{index}] destination must be a string"
                )
                continue
            path = Path(destination)
            if path.is_absolute() or ".." in path.parts:
                errors.append(
                    f"provenance manifest: unsafe destination {destination!r}"
                )
            elif not (ROOT / path).exists():
                errors.append(f"provenance manifest: missing destination {destination}")
    return errors


def check_licensing_hold() -> list[str]:
    errors: list[str] = []
    for name in ("LICENSE", "LICENSE.txt", "LICENSE.md", "COPYING"):
        if (ROOT / name).exists():
            errors.append(f"definitive license file exists without approval: {name}")
    proposal = ROOT / "LICENSING-PROPOSAL.md"
    if (
        not proposal.is_file()
        or "not a license" not in proposal.read_text(encoding="utf-8").lower()
    ):
        errors.append("licensing proposal does not state that it is not a license")
    return errors


def check_ci_matrix() -> list[str]:
    if not CI_WORKFLOW.is_file():
        return ["CI workflow is missing"]
    text = CI_WORKFLOW.read_text(encoding="utf-8")
    required = (
        "ubuntu-24.04",
        "macos-15",
        "windows-2025",
        '"3.11"',
        '"3.12"',
        '"3.13"',
        "persist-credentials: false",
    )
    return [f"CI workflow is missing {item}" for item in required if item not in text]


def main() -> int:
    errors = [
        *check_core_neutrality(),
        *check_sensitive_content(),
        *check_provenance(),
        *check_licensing_hold(),
        *check_packaged_schemas(),
        *check_module_schemas(),
        *check_ci_matrix(),
    ]
    if errors:
        for error in sorted(set(errors)):
            print(f"ERROR: {error}")
        return 1
    print("Extraction contracts: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
