"""Passive Codex envelope adapter.

The adapter prepares portable data only. It does not launch a task, select a
model, contact a service, or grant authority for an external effect.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from stateweave.core.errors import ContractError
from stateweave.runtime import DispatchEnvelope
from stateweave.runtime.model import SLUG


@dataclass(frozen=True)
class CodexAdapter:
    """Translate a runtime-neutral envelope into a host-consumable document."""

    supported_capabilities: frozenset[str]

    def __init__(self, capabilities: Iterable[str]) -> None:
        materialized = frozenset(capabilities)
        if not materialized:
            raise ContractError("Codex adapter requires at least one capability")
        if any(
            not isinstance(capability, str) or SLUG.fullmatch(capability) is None
            for capability in materialized
        ):
            raise ContractError("Codex adapter capabilities must be portable slugs")
        object.__setattr__(self, "supported_capabilities", materialized)

    @property
    def adapter_id(self) -> str:
        return "codex"

    @property
    def capabilities(self) -> frozenset[str]:
        return self.supported_capabilities

    def prepare(self, envelope: DispatchEnvelope) -> dict[str, Any]:
        return {
            **envelope.as_dict(),
            "runtime": self.adapter_id,
            "execution_authorized": False,
        }

    def receipt_observation(
        self,
        *,
        implementation: str,
        model_id: str | None,
    ) -> dict[str, str | None]:
        """Build the only surface where a concrete model may be observed."""

        if not implementation.strip() or len(implementation) > 200:
            raise ContractError("implementation must be a non-empty short string")
        if model_id is not None and (not model_id.strip() or len(model_id) > 200):
            raise ContractError("model_id must be null or a non-empty short string")
        return {
            "adapter": self.adapter_id,
            "implementation": implementation,
            "model_id": model_id,
        }
