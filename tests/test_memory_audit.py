from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from stateweave.core.audit import LoadedRecord, _supersession_cycle, audit_repository
from stateweave.core.backup import project_writer_lock
from stateweave.core.config import load_config

from tests.helpers import fact, project, write_fact


class MemoryAuditTests(unittest.TestCase):
    def test_reciprocal_supersession_and_backlinks_pass(self) -> None:
        with TemporaryDirectory() as temporary:
            config = project(Path(temporary) / "memory")
            write_fact(
                config,
                fact(
                    "FCT-old",
                    status="deprecated",
                    superseded_by="FCT-new",
                    value="old",
                ),
            )
            write_fact(
                config,
                fact(
                    "FCT-new",
                    supersedes=["FCT-old"],
                    references=["FCT-old"],
                    value="new",
                ),
            )

            report = audit_repository(config, today=date(2026, 7, 25))

            self.assertTrue(report.ok, report.errors)
            links = {
                (item.source_id, item.relation) for item in report.backlinks["FCT-old"]
            }
            self.assertIn(("FCT-new", "supersedes"), links)
            self.assertIn(("FCT-new", "references"), links)
            self.assertFalse(report.conflicts)

    def test_nonreciprocal_supersession_is_rejected(self) -> None:
        with TemporaryDirectory() as temporary:
            config = project(Path(temporary) / "memory")
            write_fact(config, fact("FCT-old", value="old"))
            write_fact(
                config,
                fact("FCT-new", supersedes=["FCT-old"], value="new"),
            )

            report = audit_repository(config, today=date(2026, 7, 25))

            self.assertTrue(
                any("not reciprocal" in error for error in report.errors),
                report.errors,
            )

    def test_supersession_cycle_is_rejected(self) -> None:
        with TemporaryDirectory() as temporary:
            config = project(Path(temporary) / "memory")
            write_fact(
                config,
                fact(
                    "FCT-one",
                    status="deprecated",
                    supersedes=["FCT-two"],
                    superseded_by="FCT-two",
                ),
            )
            write_fact(
                config,
                fact(
                    "FCT-two",
                    status="deprecated",
                    supersedes=["FCT-one"],
                    superseded_by="FCT-one",
                ),
            )
            report = audit_repository(config, today=date(2026, 7, 25))
            self.assertTrue(
                any("supersession cycle" in error for error in report.errors)
            )

    def test_structured_conflict_is_deterministic(self) -> None:
        with TemporaryDirectory() as temporary:
            config = project(Path(temporary) / "memory")
            write_fact(config, fact("FCT-one", value={"enabled": True}))
            write_fact(config, fact("FCT-two", value={"enabled": False}))

            first = audit_repository(config, today=date(2026, 7, 25))
            second = audit_repository(config, today=date(2026, 7, 25))

            self.assertEqual(first.as_dict(), second.as_dict())
            self.assertEqual(len(first.conflicts), 1)
            self.assertIn("conflict:", first.errors[0])

    def test_configured_ttl_drives_stale_and_due_soon_queue(self) -> None:
        with TemporaryDirectory() as temporary:
            config = project(Path(temporary) / "memory")
            stale = fact(
                "FCT-stale",
                verified_at="2026-01-01T00:00:00Z",
                review_after=None,
            )
            due = fact(
                "FCT-due",
                verified_at="2026-07-01T00:00:00Z",
                review_after="2026-07-30",
            )
            due["fact_class"] = "volatile"
            write_fact(config, stale)
            write_fact(config, due)

            report = audit_repository(config, today=date(2026, 7, 25))

            self.assertTrue(
                any("stale verified fact" in item for item in report.errors)
            )
            reasons = {(item["id"], item["reason"]) for item in report.review_queue}
            self.assertIn(("FCT-stale", "stale"), reasons)
            self.assertIn(("FCT-due", "due_soon"), reasons)

    def test_ttl_ceiling_and_unknown_role_are_rejected(self) -> None:
        with TemporaryDirectory() as temporary:
            config = project(Path(temporary) / "memory")
            payload = fact(
                "FCT-long",
                verified_at="2026-07-01T00:00:00Z",
                review_after="2027-07-01",
            )
            payload["owner_role"] = "fixed-project-authority"
            write_fact(config, payload)
            report = audit_repository(config, today=date(2026, 7, 25))
            self.assertTrue(any("exceeds configured" in item for item in report.errors))
            self.assertTrue(any("is not configured" in item for item in report.errors))

    def test_active_writer_blocks_external_audit(self) -> None:
        with TemporaryDirectory() as temporary:
            config = project(Path(temporary) / "memory")
            with project_writer_lock(config):
                blocked = audit_repository(config, today=date(2026, 7, 25))
                internal = audit_repository(
                    config,
                    today=date(2026, 7, 25),
                    allow_active_writer=True,
                )
            self.assertFalse(blocked.ok)
            self.assertIn("writer lock is active", blocked.errors[0])
            self.assertTrue(internal.ok, internal.errors)

    def test_symlink_record_is_rejected(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = project(root / "memory")
            outside = root / "outside.json"
            outside.write_text("{}", encoding="utf-8")
            link = config.facts_dir / "FCT-linked.json"
            try:
                link.symlink_to(outside)
            except OSError as exc:
                self.skipTest(f"symlink creation unavailable: {exc}")
            report = audit_repository(config, today=date(2026, 7, 25))
            self.assertTrue(
                any("may not be a symlink" in item for item in report.errors)
            )

    def test_configured_record_count_limit_fails_closed(self) -> None:
        with TemporaryDirectory() as temporary:
            config = project(Path(temporary) / "memory")
            write_fact(config, fact("FCT-extra"))
            text = config.source.read_text(encoding="utf-8")
            config.source.write_text(
                text.replace("max_records = 10000", "max_records = 1"),
                encoding="utf-8",
            )
            limited = load_config(config.root)
            report = audit_repository(limited, today=date(2026, 7, 25))
            self.assertFalse(report.ok)
            self.assertIn("exceeds configured limit", report.errors[0])

    def test_unexpected_record_area_content_is_not_silently_ignored(self) -> None:
        with TemporaryDirectory() as temporary:
            config = project(Path(temporary) / "memory")
            nested = config.facts_dir / "nested"
            nested.mkdir()
            (nested / "FCT-hidden.json").write_text("{not-json}", encoding="utf-8")
            (config.decisions_dir / "notes.txt").write_text(
                "synthetic unexpected content",
                encoding="utf-8",
            )

            report = audit_repository(config)

            self.assertFalse(report.ok)
            joined = "; ".join(report.errors)
            self.assertIn("memory/facts/nested: unexpected directory", joined)
            self.assertIn("memory/decisions/notes.txt: unexpected non-JSON", joined)

    def test_missing_state_record_fails_closed(self) -> None:
        with TemporaryDirectory() as temporary:
            config = project(Path(temporary) / "memory")
            config.state_file.unlink()

            report = audit_repository(config)

            self.assertFalse(report.ok)
            self.assertIn(
                "configured state record is missing",
                "; ".join(report.errors),
            )

    def test_large_supersession_graph_does_not_depend_on_python_recursion(self) -> None:
        records: dict[str, LoadedRecord] = {}
        count = 1500
        for index in range(count):
            identifier = f"FCT-{index:04d}"
            target = f"FCT-{index + 1:04d}" if index + 1 < count else "FCT-0000"
            records[identifier] = LoadedRecord(
                identifier=identifier,
                kind="fact",
                path=Path(f"{identifier}.json"),
                data={"supersedes": [target]},
            )
        cycle = _supersession_cycle(records)
        self.assertIsNotNone(cycle)
        assert cycle is not None
        self.assertEqual(cycle[0], cycle[-1])


if __name__ == "__main__":
    unittest.main()
