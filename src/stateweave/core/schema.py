"""Official Draft 2020-12 validation for packaged StateWeave contracts."""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator, FormatChecker  # type: ignore[import-untyped]
from jsonschema.exceptions import SchemaError  # type: ignore[import-untyped]

SCHEMA_NAMES = ("config", "decision", "fact", "state", "transaction")
RECORD_SCHEMA_NAMES = frozenset({"decision", "fact", "state"})
_RFC3339_DATETIME = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}"
    r"(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)
_URI_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*$")

FORMAT_CHECKER = FormatChecker()


@FORMAT_CHECKER.checks("date-time")
def _is_rfc3339_datetime(value: object) -> bool:
    if not isinstance(value, str):
        return True
    if _RFC3339_DATETIME.fullmatch(value) is None:
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


@FORMAT_CHECKER.checks("date")
def _is_iso_date(value: object) -> bool:
    if not isinstance(value, str):
        return True
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


@FORMAT_CHECKER.checks("uri")
def _is_absolute_uri(value: object) -> bool:
    if not isinstance(value, str):
        return True
    if not value or any(char.isspace() for char in value):
        return False
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    return _URI_SCHEME.fullmatch(parsed.scheme) is not None


def _schema_resource(name: str) -> resources.abc.Traversable:
    if name not in SCHEMA_NAMES:
        raise ValueError(f"unknown schema name: {name!r}")
    return resources.files("stateweave.core").joinpath(
        "schemas",
        f"{name}.schema.json",
    )


@lru_cache(maxsize=len(SCHEMA_NAMES))
def _validator(name: str) -> Draft202012Validator:
    resource = _schema_resource(name)
    try:
        payload = json.loads(resource.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read packaged schema {name!r}: {exc}") from exc
    Draft202012Validator.check_schema(payload)
    return Draft202012Validator(payload, format_checker=FORMAT_CHECKER)


def check_packaged_schemas() -> list[str]:
    """Return deterministic errors for malformed or missing packaged schemas."""

    errors: list[str] = []
    for name in SCHEMA_NAMES:
        try:
            _validator(name)
        except (OSError, ValueError, SchemaError) as exc:
            errors.append(f"{name}: {exc}")
    return errors


def validate_payload(
    payload: Any,
    schema_name: str,
    source: str | Path,
) -> list[str]:
    """Validate one payload and return stable, source-labelled errors."""

    try:
        validator = _validator(schema_name)
    except (OSError, ValueError, SchemaError) as exc:
        return [f"{source}: cannot load {schema_name} schema: {exc}"]
    errors = sorted(
        validator.iter_errors(payload),
        key=lambda error: (error.json_path, error.validator or "", error.message),
    )
    return [
        f"{source}: {schema_name} schema {error.json_path}: {error.message}"
        for error in errors
    ]


def validate_config(payload: Any, source: str | Path) -> list[str]:
    """Validate a parsed TOML configuration with the packaged contract."""

    return validate_payload(payload, "config", source)


def validate_record(
    payload: dict[str, Any],
    expected_kind: str,
    source: Path,
) -> list[str]:
    """Validate a fact, decision, or state record with its official schema."""

    if expected_kind not in RECORD_SCHEMA_NAMES:
        return [f"{source}: unsupported record schema {expected_kind!r}"]
    return validate_payload(payload, expected_kind, source)
