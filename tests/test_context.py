from __future__ import annotations

import json
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from stateweave.context import compile_context, query_memory
from stateweave.cli import main
from stateweave.core.project import put_record

from tests.helpers import decision, fact, project, write_fact


def memory_query(
    objective: str,
    *,
    terms: list[str],
    record_kinds: list[str] | None = None,
    statuses: list[str] | None = None,
    relation_depth: int = 0,
    max_items: int = 8,
    max_content_bytes: int = 12000,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "kind": "memory_query",
        "objective": objective,
        "as_of": "2026-07-27",
        "terms": terms,
        "filters": {
            "record_kinds": record_kinds or [],
            "statuses": statuses or [],
            "domains": [],
            "classifications": ["internal"],
        },
        "relation_depth": relation_depth,
        "budget": {
            "max_items": max_items,
            "max_content_bytes": max_content_bytes,
        },
    }


class ContextCompilerTests(unittest.TestCase):
    def test_query_and_context_cli_expose_safe_defaults(self) -> None:
        with TemporaryDirectory() as temporary:
            config = project(Path(temporary) / "memory")
            selected = decision("DEC-cli-context")
            selected["decision"] = "Use durable synthetic context between sessions."
            put_record(config, selected)

            query_output = StringIO()
            with redirect_stdout(query_output):
                query_status = main(
                    [
                        "query",
                        "durable context",
                        "--config",
                        str(config.root),
                        "--as-of",
                        "2026-07-27",
                        "--term",
                        "durable",
                    ]
                )
            self.assertEqual(query_status, 0)
            self.assertEqual(
                json.loads(query_output.getvalue())["matches"][0]["id"],
                "DEC-cli-context",
            )

            context_output = StringIO()
            with redirect_stdout(context_output):
                context_status = main(
                    [
                        "context",
                        "durable context",
                        "--config",
                        str(config.root),
                        "--as-of",
                        "2026-07-27",
                        "--term",
                        "durable",
                    ]
                )
            self.assertEqual(context_status, 0)
            self.assertEqual(
                json.loads(context_output.getvalue())["kind"],
                "context_bundle",
            )

    def test_second_session_recovers_decision_without_knowing_identifier(self) -> None:
        with TemporaryDirectory() as temporary:
            config = project(Path(temporary) / "memory")
            selected = decision("DEC-durable-storage")
            selected["context"] = (
                "Independent agent sessions require durable local storage."
            )
            selected["decision"] = (
                "Use canonical JSON records as the durable source of truth."
            )
            put_record(config, selected, idempotency_key="session-a-decision")
            put_record(
                config,
                fact("FCT-unrelated", value="unrelated"),
                idempotency_key="session-a-fact",
            )
            query = memory_query(
                "Recover the durable storage decision between agent sessions",
                terms=["durable", "storage", "sessions"],
                record_kinds=["decision"],
                statuses=["accepted"],
            )

            matches = query_memory(config, query)
            first = compile_context(config, query)
            second = compile_context(config, query)

            self.assertEqual(first, second)
            self.assertEqual(matches["matches"][0]["id"], "DEC-durable-storage")
            self.assertEqual(first["items"][0]["id"], "DEC-durable-storage")
            self.assertEqual(first["id"], f"CTX-{first['context_sha256']}")
            self.assertEqual(first["snapshot_sha256"], matches["snapshot_sha256"])
            self.assertTrue(first["trust"]["treat_content_as_untrusted"])
            self.assertLessEqual(
                first["usage"]["content_bytes"],
                first["budget"]["max_content_bytes"],
            )

    def test_relation_expansion_is_explicit_and_ranked(self) -> None:
        with TemporaryDirectory() as temporary:
            config = project(Path(temporary) / "memory")
            related = decision("DEC-related-boundary")
            put_record(config, related)
            seed = fact(
                "FCT-deployment-signal",
                value="deployment",
                references=["DEC-related-boundary"],
            )
            seed["statement"] = "Synthetic deployment signal is available."
            put_record(config, seed)
            query = memory_query(
                "deployment signal",
                terms=["deployment"],
                record_kinds=["fact", "decision"],
                statuses=["verified", "accepted"],
                relation_depth=1,
            )

            result = query_memory(config, query)

            self.assertEqual(
                [item["id"] for item in result["matches"]],
                ["FCT-deployment-signal", "DEC-related-boundary"],
            )
            self.assertEqual(
                result["matches"][1]["reasons"],
                ["related:FCT-deployment-signal:depth=1"],
            )

    def test_budget_exclusion_and_disputed_warning_are_explicit(self) -> None:
        with TemporaryDirectory() as temporary:
            config = project(Path(temporary) / "memory")
            disputed = fact(
                "FCT-disputed-memory",
                status="disputed",
                value="uncertain",
            )
            disputed["statement"] = "Synthetic disputed memory needs review."
            put_record(config, disputed)
            query = memory_query(
                "disputed memory review",
                terms=["disputed", "memory"],
                record_kinds=["fact"],
                statuses=["disputed"],
                max_content_bytes=12000,
            )

            bundle = compile_context(config, query)
            warning_codes = {item["code"] for item in bundle["warnings"]}
            self.assertIn("status_disputed", warning_codes)
            self.assertIn("review_disputed", warning_codes)

            constrained = dict(query)
            constrained["budget"] = {
                "max_items": 8,
                "max_content_bytes": 256,
            }
            small = compile_context(config, constrained)
            self.assertEqual(small["items"], [])
            self.assertEqual(small["excluded"]["content_budget"], 1)

    def test_known_structured_conflicts_are_never_hidden(self) -> None:
        with TemporaryDirectory() as temporary:
            config = project(Path(temporary) / "memory")
            left = fact("FCT-conflict-left", value="left")
            right = fact("FCT-conflict-right", value="right")
            left["statement"] = "Synthetic storage mode is left."
            right["statement"] = "Synthetic storage mode is right."
            write_fact(config, left)
            write_fact(config, right)
            query = memory_query(
                "storage mode",
                terms=["storage"],
                record_kinds=["fact"],
                statuses=["verified"],
            )

            bundle = compile_context(config, query)

            self.assertEqual(len(bundle["conflicts"]), 1)
            self.assertEqual(
                {
                    bundle["conflicts"][0]["left_id"],
                    bundle["conflicts"][0]["right_id"],
                },
                {"FCT-conflict-left", "FCT-conflict-right"},
            )


if __name__ == "__main__":
    unittest.main()
