"""Runtime-neutral dispatch preparation with no implicit execution."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping, Protocol, runtime_checkable

from stateweave.core.errors import ContractError

TASK_ID = re.compile(r"^TSK-[A-Za-z0-9][A-Za-z0-9._-]{1,127}$")
SHA256 = re.compile(r"^[a-f0-9]{64}$")
SLUG = re.compile(r"^[a-z][a-z0-9-]{1,63}$")


@dataclass(frozen=True)
class DispatchEnvelope:
    """Validated, content-bound input presented to a runtime adapter."""

    task_id: str
    objective: str
    input_manifest_sha256: str
    allowed_effects: tuple[str, ...] = ()
    metadata: Mapping[str, str | int | bool | None] = field(default_factory=dict)

    def __post_init__(self) -> None:
        errors: list[str] = []
        if TASK_ID.fullmatch(self.task_id) is None:
            errors.append(f"invalid task id {self.task_id!r}")
        if len(self.objective.strip()) < 10 or len(self.objective) > 20000:
            errors.append("objective must contain 10 to 20000 characters")
        if SHA256.fullmatch(self.input_manifest_sha256) is None:
            errors.append("input_manifest_sha256 must be a lowercase SHA-256")
        if len(set(self.allowed_effects)) != len(self.allowed_effects):
            errors.append("allowed_effects must be unique")
        if any(
            not isinstance(effect, str) or SLUG.fullmatch(effect) is None
            for effect in self.allowed_effects
        ):
            errors.append("allowed_effects must contain portable slugs")
        if len(self.metadata) > 50:
            errors.append("metadata exceeds 50 fields")
        for key, value in self.metadata.items():
            if not isinstance(key, str) or SLUG.fullmatch(key) is None:
                errors.append(f"invalid metadata key {key!r}")
                continue
            if isinstance(value, str) and len(value) > 500:
                errors.append(f"metadata field {key!r} exceeds 500 characters")
            elif value is not None and not isinstance(value, (str, int, bool)):
                errors.append(f"metadata field {key!r} must be scalar")
        if errors:
            raise ContractError("; ".join(errors))
        object.__setattr__(
            self,
            "metadata",
            MappingProxyType(dict(self.metadata)),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "task_id": self.task_id,
            "objective": self.objective,
            "input_manifest_sha256": self.input_manifest_sha256,
            "allowed_effects": list(self.allowed_effects),
            "metadata": dict(self.metadata),
        }


@runtime_checkable
class RuntimeAdapter(Protocol):
    """Adapter surface; hosts decide whether and when execution occurs."""

    @property
    def adapter_id(self) -> str: ...

    @property
    def capabilities(self) -> frozenset[str]: ...

    def prepare(self, envelope: DispatchEnvelope) -> dict[str, Any]: ...


class RuntimeRegistry:
    """In-memory registry that never imports or dispatches adapters implicitly."""

    def __init__(self) -> None:
        self._adapters: dict[str, RuntimeAdapter] = {}

    def register(self, adapter: RuntimeAdapter) -> None:
        if SLUG.fullmatch(adapter.adapter_id) is None:
            raise ContractError(f"invalid adapter id {adapter.adapter_id!r}")
        if adapter.adapter_id in self._adapters:
            raise ContractError(f"duplicate runtime adapter {adapter.adapter_id!r}")
        self._adapters[adapter.adapter_id] = adapter

    def get(self, adapter_id: str) -> RuntimeAdapter:
        try:
            return self._adapters[adapter_id]
        except KeyError as exc:
            raise ContractError(f"unknown runtime adapter {adapter_id!r}") from exc

    def eligible(self, required_capabilities: set[str]) -> tuple[RuntimeAdapter, ...]:
        return tuple(
            adapter
            for _, adapter in sorted(self._adapters.items())
            if required_capabilities.issubset(adapter.capabilities)
        )
