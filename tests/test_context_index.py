from __future__ import annotations

import json
import unittest
from contextlib import redirect_stdout
from datetime import date
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from stateweave.cli import main
from stateweave.context import (
    build_context_index,
    compile_context,
    inspect_context_index,
)
from stateweave.core.io import atomic_write_json, read_json

from tests.helpers import fact, project, write_fact
from tests.test_context import memory_query


class ContextIndexTests(unittest.TestCase):
    def test_verified_index_reproduces_scan_without_running_full_audit(self) -> None:
        with TemporaryDirectory() as temporary:
            config = project(Path(temporary) / "memory")
            selected = fact("FCT-indexed-context", value="stable")
            selected["statement"] = "Synthetic indexed context remains stable."
            write_fact(config, selected)
            query = memory_query(
                "indexed context",
                terms=["indexed"],
                record_kinds=["fact"],
                statuses=["verified"],
            )
            scanned = compile_context(config, query)
            build_context_index(config, as_of=date(2026, 7, 27))

            with (
                patch(
                    "stateweave.context.compiler.audit_repository",
                    side_effect=AssertionError("full audit should not run"),
                ),
                patch(
                    "stateweave.context.compiler.load_records",
                    side_effect=AssertionError("full record load should not run"),
                ),
            ):
                indexed = compile_context(config, query)

            self.assertEqual(indexed, scanned)
            status = inspect_context_index(
                config,
                as_of=date(2026, 7, 27),
            )
            self.assertTrue(status["valid"])
            self.assertEqual(status["record_count"], 2)

    def test_record_drift_invalidates_index_and_forces_safe_fallback(self) -> None:
        with TemporaryDirectory() as temporary:
            config = project(Path(temporary) / "memory")
            write_fact(config, fact("FCT-before-index", value="value"))
            as_of = date(2026, 7, 27)
            build_context_index(config, as_of=as_of)

            added = fact("FCT-after-index", value="value")
            added["statement"] = "Synthetic fallback record is discoverable."
            write_fact(config, added)
            status = inspect_context_index(config, as_of=as_of)
            query = memory_query(
                "fallback record",
                terms=["fallback"],
                record_kinds=["fact"],
                statuses=["verified"],
            )
            bundle = compile_context(config, query)

            self.assertFalse(status["valid"])
            self.assertEqual(bundle["items"][0]["id"], "FCT-after-index")

    def test_tampered_index_is_rejected_and_cli_reports_invalid(self) -> None:
        with TemporaryDirectory() as temporary:
            config = project(Path(temporary) / "memory")
            write_fact(config, fact("FCT-index-tamper", value="stable"))
            as_of = date(2026, 7, 27)
            path = build_context_index(config, as_of=as_of)
            payload = read_json(path, max_bytes=128 * 1024 * 1024)
            payload["records"][0]["content"]["classification"] = "tampered"
            atomic_write_json(path, payload)

            output = StringIO()
            with redirect_stdout(output):
                status = main(
                    [
                        "index-status",
                        "--config",
                        str(config.root),
                        "--as-of",
                        "2026-07-27",
                    ]
                )

            self.assertEqual(status, 1)
            self.assertFalse(json.loads(output.getvalue())["valid"])


if __name__ == "__main__":
    unittest.main()
