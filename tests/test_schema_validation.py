from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from stateweave.core.audit import audit_repository
from stateweave.core.config import load_config
from stateweave.core.errors import ConfigurationError, RecordError
from stateweave.core.io import atomic_write_json
from stateweave.core.project import put_record
from stateweave.core.schema import check_packaged_schemas

from tests.helpers import fact, project


class OfficialSchemaValidationTests(unittest.TestCase):
    def test_all_packaged_schemas_are_valid_draft_2020_12(self) -> None:
        self.assertEqual(check_packaged_schemas(), [])

    def test_audit_rejects_required_const_and_additional_property_violations(
        self,
    ) -> None:
        mutations = (
            ("missing title", lambda payload: payload.pop("title")),
            (
                "wrong schema version",
                lambda payload: payload.update(schema_version="0.9"),
            ),
            (
                "additional property",
                lambda payload: payload.update(unexpected=True),
            ),
        )
        for label, mutate in mutations:
            with self.subTest(label=label), TemporaryDirectory() as temporary:
                config = project(Path(temporary) / "memory")
                payload = fact("FCT-invalid")
                mutate(payload)
                atomic_write_json(config.facts_dir / "FCT-invalid.json", payload)

                report = audit_repository(config)

                self.assertFalse(report.ok)
                self.assertTrue(
                    any("fact schema" in error for error in report.errors),
                    report.errors,
                )

    def test_audit_asserts_formats_and_unique_items(self) -> None:
        with TemporaryDirectory() as temporary:
            config = project(Path(temporary) / "memory")
            payload = fact(
                "FCT-invalid",
                verified_at="not-a-date-time",
                references=["FCT-other", "FCT-other"],
            )
            payload["sources"][0]["uri"] = "not a uri"
            atomic_write_json(config.facts_dir / "FCT-invalid.json", payload)

            report = audit_repository(config)

            joined = "\n".join(report.errors)
            self.assertIn("is not a 'date-time'", joined)
            self.assertIn("is not a 'uri'", joined)
            self.assertIn("has non-unique elements", joined)

    def test_malformed_uri_format_is_reported_without_checker_crash(self) -> None:
        with TemporaryDirectory() as temporary:
            config = project(Path(temporary) / "memory")
            payload = fact("FCT-invalid-uri")
            payload["sources"][0]["uri"] = "http://["
            atomic_write_json(
                config.facts_dir / "FCT-invalid-uri.json",
                payload,
            )

            report = audit_repository(config)

            self.assertFalse(report.ok)
            self.assertTrue(
                any("is not a 'uri'" in error for error in report.errors),
                report.errors,
            )

    def test_put_record_validates_before_any_write(self) -> None:
        with TemporaryDirectory() as temporary:
            config = project(Path(temporary) / "memory")
            payload = fact("FCT-invalid")
            del payload["statement"]

            with self.assertRaises(RecordError):
                put_record(config, payload)

            self.assertFalse((config.facts_dir / "FCT-invalid.json").exists())

    def test_config_schema_rejects_portable_traversal_and_unknown_keys(self) -> None:
        mutations = (
            (
                "windows traversal",
                lambda text: text.replace(
                    'facts = "memory/facts"',
                    'facts = "..\\\\outside"',
                ),
            ),
            (
                "unknown property",
                lambda text: text.replace(
                    "[project]\n",
                    '[project]\nunknown = "value"\n',
                ),
            ),
        )
        for label, mutate in mutations:
            with self.subTest(label=label), TemporaryDirectory() as temporary:
                config = project(Path(temporary) / "memory")
                text = config.source.read_text(encoding="utf-8")
                config.source.write_text(mutate(text), encoding="utf-8")

                with self.assertRaises(ConfigurationError) as caught:
                    load_config(config.root)

                self.assertIn("official Draft 2020-12 schema", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
