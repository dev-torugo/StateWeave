from __future__ import annotations

import math
import unittest
from datetime import UTC, datetime, timedelta

from stateweave.adapters import CodexAdapter
from stateweave.core.errors import ContractError
from stateweave.runtime import DispatchEnvelope, RuntimeRegistry
from stateweave.telemetry import (
    ReadOnlyObserver,
    TelemetryBuffer,
    TelemetryPolicy,
)


class RuntimeAndTelemetryTests(unittest.TestCase):
    def test_codex_adapter_prepares_but_never_authorizes_execution(self) -> None:
        envelope = DispatchEnvelope(
            task_id="TSK-synthetic",
            objective="Inspect a synthetic repository without external effects.",
            input_manifest_sha256="0" * 64,
            allowed_effects=("read-repository",),
            metadata={"risk": "low"},
        )
        adapter = CodexAdapter(("repository-read", "test-run"))

        prepared = adapter.prepare(envelope)
        observation = adapter.receipt_observation(
            implementation="synthetic-host",
            model_id="observed-model-id",
        )

        self.assertFalse(prepared["execution_authorized"])
        self.assertNotIn("model_id", prepared)
        self.assertEqual(observation["model_id"], "observed-model-id")

    def test_runtime_registry_is_explicit_unique_and_capability_aware(self) -> None:
        adapter = CodexAdapter(("repository-read", "test-run"))
        registry = RuntimeRegistry()
        registry.register(adapter)

        eligible = registry.eligible({"test-run"})

        self.assertEqual(eligible, (adapter,))
        with self.assertRaises(ContractError):
            registry.register(adapter)

    def test_invalid_dispatch_envelope_fails_closed(self) -> None:
        with self.assertRaises(ContractError):
            DispatchEnvelope(
                task_id="../../escape",
                objective="short",
                input_manifest_sha256="not-a-hash",
                allowed_effects=("invalid_effect",),
            )
        with self.assertRaises(ContractError):
            CodexAdapter((object(),))

    def test_telemetry_is_opt_in_and_allow_listed(self) -> None:
        disabled = TelemetryBuffer(
            TelemetryPolicy(
                enabled=False,
                allowed_fields=frozenset({"duration-ms"}),
                retention_days=30,
            )
        )
        with self.assertRaises(ContractError):
            disabled.add(
                event="task-finished",
                occurred_at=datetime.now(UTC),
                fields={"duration-ms": 10},
            )

        enabled = TelemetryBuffer(
            TelemetryPolicy(
                enabled=True,
                allowed_fields=frozenset({"duration-ms", "status"}),
                retention_days=30,
            )
        )
        with self.assertRaises(ContractError):
            enabled.add(
                event="task-finished",
                occurred_at=datetime.now(UTC),
                fields={"prompt": "must never be collected"},
            )
        with self.assertRaises(ContractError):
            enabled.add(
                event="task-finished",
                occurred_at=datetime.now(UTC),
                fields={"duration-ms": math.inf},
            )

    def test_observer_aggregates_and_retention_prunes_locally(self) -> None:
        policy = TelemetryPolicy(
            enabled=True,
            allowed_fields=frozenset({"duration-ms", "status"}),
            retention_days=7,
        )
        buffer = TelemetryBuffer(policy)
        now = datetime(2026, 7, 25, 12, 0, tzinfo=UTC)
        buffer.add(
            event="task-finished",
            occurred_at=now - timedelta(days=8),
            fields={"duration-ms": 50, "status": "succeeded"},
        )
        buffer.add(
            event="task-finished",
            occurred_at=now,
            fields={"duration-ms": 40, "status": "succeeded"},
        )

        removed = buffer.prune(now=now)
        summary = ReadOnlyObserver().summarize(buffer)

        self.assertEqual(removed, 1)
        self.assertEqual(summary.total, 1)
        self.assertEqual(summary.by_event, {"task-finished": 1})


if __name__ == "__main__":
    unittest.main()
