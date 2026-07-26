"""Bounded local telemetry that rejects non-allow-listed data."""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from typing import Any, Mapping

from stateweave.core.errors import ContractError

SLUG = re.compile(r"^[a-z][a-z0-9-]{1,63}$")
SENSITIVE_KEY = re.compile(
    r"(?:secret|token|password|credential|prompt|message|email|phone)",
    re.IGNORECASE,
)
Scalar = str | int | float | bool | None


@dataclass(frozen=True)
class TelemetryPolicy:
    enabled: bool
    allowed_fields: frozenset[str]
    retention_days: int
    max_observations: int = 10000

    def __post_init__(self) -> None:
        if self.retention_days < 1 or self.retention_days > 3650:
            raise ContractError("retention_days must be between 1 and 3650")
        if self.max_observations < 1 or self.max_observations > 1_000_000:
            raise ContractError("max_observations must be between 1 and 1000000")
        for field_name in self.allowed_fields:
            if SLUG.fullmatch(field_name) is None:
                raise ContractError(f"invalid telemetry field {field_name!r}")
            if SENSITIVE_KEY.search(field_name):
                raise ContractError(
                    f"sensitive telemetry field is forbidden: {field_name!r}"
                )


@dataclass(frozen=True)
class Observation:
    event: str
    occurred_at: datetime
    fields: Mapping[str, Scalar]


@dataclass(frozen=True)
class ObservationSummary:
    total: int
    by_event: Mapping[str, int]
    oldest: datetime | None
    newest: datetime | None


class TelemetryBuffer:
    """A bounded memory buffer; persistence is an explicit consumer choice."""

    def __init__(self, policy: TelemetryPolicy) -> None:
        self.policy = policy
        self._items: list[Observation] = []

    @staticmethod
    def _validate_scalar(field_name: str, value: Any) -> Scalar:
        if value is not None and not isinstance(value, (str, int, float, bool)):
            raise ContractError(f"telemetry field {field_name!r} must be a scalar")
        if isinstance(value, float) and not math.isfinite(value):
            raise ContractError(f"telemetry field {field_name!r} must be finite")
        if isinstance(value, str) and (
            len(value) > 200 or "\n" in value or "\r" in value
        ):
            raise ContractError(
                f"telemetry field {field_name!r} must be a short single line"
            )
        return value

    def add(
        self,
        *,
        event: str,
        occurred_at: datetime,
        fields: Mapping[str, Any],
    ) -> Observation:
        if not self.policy.enabled:
            raise ContractError("telemetry is disabled by policy")
        if SLUG.fullmatch(event) is None:
            raise ContractError(f"invalid telemetry event {event!r}")
        if occurred_at.tzinfo is None or occurred_at.utcoffset() is None:
            raise ContractError("occurred_at must be timezone-aware")
        if len(self._items) >= self.policy.max_observations:
            raise ContractError("telemetry buffer limit reached")
        unknown = sorted(set(fields) - self.policy.allowed_fields)
        if unknown:
            raise ContractError(f"telemetry fields are not allow-listed: {unknown}")
        cleaned = {
            key: self._validate_scalar(key, value)
            for key, value in sorted(fields.items())
        }
        observation = Observation(
            event=event,
            occurred_at=occurred_at.astimezone(UTC),
            fields=MappingProxyType(cleaned),
        )
        self._items.append(observation)
        return observation

    def prune(self, *, now: datetime) -> int:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ContractError("now must be timezone-aware")
        cutoff = now.astimezone(UTC) - timedelta(days=self.policy.retention_days)
        before = len(self._items)
        self._items = [item for item in self._items if item.occurred_at >= cutoff]
        return before - len(self._items)

    def snapshot(self) -> tuple[Observation, ...]:
        return tuple(self._items)


class ReadOnlyObserver:
    """Aggregate a buffer without exposing mutation or external I/O."""

    def summarize(self, buffer: TelemetryBuffer) -> ObservationSummary:
        items = buffer.snapshot()
        counts = Counter(item.event for item in items)
        timestamps = [item.occurred_at for item in items]
        return ObservationSummary(
            total=len(items),
            by_event=MappingProxyType(dict(sorted(counts.items()))),
            oldest=min(timestamps) if timestamps else None,
            newest=max(timestamps) if timestamps else None,
        )
