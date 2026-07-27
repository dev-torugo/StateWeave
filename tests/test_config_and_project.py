from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from stateweave.core.config import load_config
from stateweave.core.errors import ConfigurationError, RecordError
from stateweave.core.project import initialize_project, record_destination


class ConfigAndProjectTests(unittest.TestCase):
    def test_initialize_and_load_versioned_project(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary) / "consumer"
            config = initialize_project(
                root,
                project_id="consumer-one",
                project_name="Consumer One",
            )

            loaded = load_config(root)
            self.assertEqual(loaded.project_id, "consumer-one")
            self.assertEqual(loaded.roles, ("maintainer", "reviewer", "contributor"))
            self.assertTrue(config.state_file.is_file())
            self.assertTrue(config.facts_dir.is_dir())
            self.assertTrue(config.extensions_dir.is_dir())

    def test_initialize_refuses_non_empty_destination(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "owned.txt").write_text("preserve", encoding="utf-8")
            with self.assertRaises(RecordError):
                initialize_project(
                    root,
                    project_id="consumer-one",
                    project_name="Consumer One",
                )
            self.assertEqual((root / "owned.txt").read_text(), "preserve")

    def test_config_rejects_path_escape_through_official_schema(self) -> None:
        for label, invalid_path in (
            ("posix traversal", "../outside"),
            ("windows traversal", "..\\\\outside"),
        ):
            with self.subTest(label=label), TemporaryDirectory() as temporary:
                root = Path(temporary) / "consumer"
                config = initialize_project(
                    root,
                    project_id="consumer-one",
                    project_name="Consumer One",
                )
                text = config.source.read_text(encoding="utf-8")
                config.source.write_text(
                    text.replace(
                        'facts = "memory/facts"',
                        f'facts = "{invalid_path}"',
                    ),
                    encoding="utf-8",
                )
                with self.assertRaises(ConfigurationError) as caught:
                    load_config(root)
                self.assertIn(
                    "official Draft 2020-12 schema",
                    str(caught.exception),
                )

    def test_config_rejects_unknown_version_and_ttl_overlap(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary) / "consumer"
            config = initialize_project(
                root,
                project_id="consumer-one",
                project_name="Consumer One",
            )
            text = config.source.read_text(encoding="utf-8")
            config.source.write_text(
                text.replace("schema_version = 1", "schema_version = 2", 1),
                encoding="utf-8",
            )
            with self.assertRaises(ConfigurationError):
                load_config(root)

    def test_record_destination_rejects_traversal_and_unknown_kind(self) -> None:
        with TemporaryDirectory() as temporary:
            config = initialize_project(
                Path(temporary) / "consumer",
                project_id="consumer-one",
                project_name="Consumer One",
            )
            for identifier in ("../../escape", "RUN-aa", "FCT-a/b"):
                with self.subTest(identifier=identifier):
                    with self.assertRaises(RecordError):
                        record_destination(config, identifier)

    def test_project_name_is_toml_escaped(self) -> None:
        with TemporaryDirectory() as temporary:
            config = initialize_project(
                Path(temporary) / "consumer",
                project_id="consumer-one",
                project_name='Quoted "name" with a \\ slash',
            )
            self.assertEqual(
                load_config(config.root).project_name,
                'Quoted "name" with a \\ slash',
            )

    def test_config_rejects_overlapping_content_and_metadata_paths(self) -> None:
        with TemporaryDirectory() as temporary:
            config = initialize_project(
                Path(temporary) / "consumer",
                project_id="consumer-one",
                project_name="Consumer One",
            )
            text = config.source.read_text(encoding="utf-8")
            config.source.write_text(
                text.replace(
                    'decisions = "memory/decisions"',
                    'decisions = "memory/facts/archive"',
                ),
                encoding="utf-8",
            )
            with self.assertRaises(ConfigurationError):
                load_config(config.root)


if __name__ == "__main__":
    unittest.main()
