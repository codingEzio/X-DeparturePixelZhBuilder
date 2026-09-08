"""Consumer synchronization keeps declared font assets, notices, and provenance together."""

import hashlib
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from departure_pixel_zh_builder import build, sync
from departure_pixel_zh_builder.build import digest, identity
from tests import test_pipeline


class SyncTests(unittest.TestCase):
    def setUp(self):
        test_pipeline.PipelineTests.setUp(self)
        self.addCleanup(self.temp.cleanup)
        build.run_build(self.preset, self.output, formats=("ttf", "woff2"))
        self.consumer = self.root / "SampleConsumer"
        self.consumer.mkdir()
        self.manifest = self.consumer / "font-dependency.json"
        self.manifest.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "id": "sample-consumer",
                    "receipt": ".departurepixelzh-sync.json",
                    "font": {
                        "version": "0.1.0",
                        "recipe_sha256": identity(json.loads(self.preset.read_text())),
                        "families": [
                            {
                                "postscript_name": "OneFirst",
                                "sha256": digest(self.output / "OneFirst-Regular.ttf"),
                            },
                            {
                                "postscript_name": "TwoFirst",
                                "sha256": digest(self.output / "TwoFirst-Regular.ttf"),
                            },
                        ],
                    },
                    "files": [
                        {
                            "source": "OneFirst-Regular.ttf",
                            "destination": "Assets/Fonts/OneFirst-Regular.ttf",
                        },
                        {"source": "NOTICE.md", "destination": "Licenses/NOTICE.md"},
                        {"source": "OFL.txt", "destination": "Licenses/OFL.txt"},
                        {"source": "provenance.json", "destination": "Licenses/provenance.json"},
                    ],
                }
            )
        )
        (self.output / "NOTICE.md").write_text("fixture notice\n")
        (self.output / "OFL.txt").write_text("fixture license\n")
        (self.output / "Licenses").mkdir()
        (self.output / "Licenses" / "Component.txt").write_text("component notice\n")

    def apply(self, **kwargs):
        return sync.sync(self.preset, self.manifest, self.output, **kwargs)

    def test_sync_records_font_pin_and_check_is_read_only(self):
        first = self.apply()
        self.assertEqual(len(first["changed"]), 5)
        receipt = json.loads((self.consumer / ".departurepixelzh-sync.json").read_text())
        self.assertEqual(receipt["font"]["version"], "0.1.0")
        self.assertIn("recipe_sha256", receipt["font"])
        self.assertTrue((self.consumer / "Licenses/Component.txt").is_file())
        before = (self.consumer / "Assets/Fonts/OneFirst-Regular.ttf").read_bytes()
        checked = self.apply(check=True)
        self.assertTrue(checked["checked"])
        self.assertEqual(before, (self.consumer / "Assets/Fonts/OneFirst-Regular.ttf").read_bytes())

    def test_modified_destination_stops_before_other_writes(self):
        self.apply()
        font = self.consumer / "Assets/Fonts/OneFirst-Regular.ttf"
        notice = self.consumer / "Licenses/NOTICE.md"
        font.write_bytes(b"modified")
        before = notice.read_bytes()
        with self.assertRaisesRegex(ValueError, "modified or unmanaged"):
            self.apply()
        self.assertEqual(notice.read_bytes(), before)

    def test_manifest_font_drift_stops_before_writes(self):
        value = json.loads(self.manifest.read_text())
        value["font"]["families"][0]["sha256"] = "0" * 64
        self.manifest.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, "font-family checksums"):
            self.apply()
        self.assertFalse((self.consumer / "Assets").exists())

    def test_path_escape_and_symlink_parent_are_rejected(self):
        value = json.loads(self.manifest.read_text())
        value["receipt"] = "../escaped.json"
        self.manifest.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, "consumer receipt"):
            self.apply(dry_run=True)
        value["receipt"] = ".departurepixelzh-sync.json"
        value["files"][0]["destination"] = "Linked/font.ttf"
        self.manifest.write_text(json.dumps(value))
        outside = self.root / "Outside"
        outside.mkdir()
        (self.consumer / "Linked").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symbolic link or escaped"):
            self.apply(dry_run=True)

    def test_replacement_failure_rolls_back_all_consumer_files(self):
        self.apply()
        font = self.consumer / "Assets/Fonts/OneFirst-Regular.ttf"
        notice = self.consumer / "Licenses/NOTICE.md"
        receipt = self.consumer / ".departurepixelzh-sync.json"
        before = {path: path.read_bytes() for path in (font, notice, receipt)}
        (self.output / "NOTICE.md").write_text("changed fixture notice\n")
        self.assertIn("Licenses/NOTICE.md", self.apply(dry_run=True)["changed"])
        original = sync.os.replace
        replacements = 0

        def fail_second_staged_replacement(source, destination):
            nonlocal replacements
            if Path(source).name.startswith("new-"):
                replacements += 1
            if replacements == 2:
                raise OSError("injected consumer replacement failure")
            return original(source, destination)

        with (
            patch.object(sync.os, "replace", side_effect=fail_second_staged_replacement),
            self.assertRaisesRegex(OSError, "injected consumer replacement failure"),
        ):
            self.apply()
        self.assertEqual({path: path.read_bytes() for path in before}, before)

    def test_file_changed_after_preflight_is_not_replaced(self):
        self.apply()
        font = self.consumer / "Assets/Fonts/OneFirst-Regular.ttf"

        class ChangeAtLock:
            def __enter__(self):
                font.write_bytes(b"concurrent edit")

            def __exit__(self, *args):
                return False

        with (
            patch.object(sync, "lock_directory", return_value=ChangeAtLock()),
            self.assertRaisesRegex(
                ValueError, "(changed during synchronization|modified or unmanaged)"
            ),
        ):
            self.apply()
        self.assertEqual(font.read_bytes(), b"concurrent edit")

    def test_incomplete_sync_journal_is_restored_before_the_next_sync(self):
        self.apply()
        receipt_path = self.consumer / ".departurepixelzh-sync.json"
        receipt_before = json.loads(receipt_path.read_text())
        target = self.consumer / "Licenses" / "NOTICE.md"
        old_bytes, old_sha256 = target.read_bytes(), digest(target)
        new_bytes = b"interrupted replacement\n"
        new_sha256 = hashlib.sha256(new_bytes).hexdigest()
        target.write_bytes(new_bytes)
        recovery = self.consumer / ".departurepixelzh-sync-recovery" / "Licenses"
        recovery.mkdir(parents=True)
        (recovery / "NOTICE.md").write_bytes(old_bytes)
        journal = self.consumer / ".departurepixelzh-sync-journal.json"
        journal.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "manifest_id": "sample-consumer",
                    "receipt_before": receipt_before,
                    "receipt_after": receipt_before,
                    "changed": {"Licenses/NOTICE.md": {"before": old_sha256, "after": new_sha256}},
                    "retired": {},
                }
            )
        )

        self.assertTrue(sync.recover_pending_sync(self.consumer, receipt_path, "sample-consumer"))

        self.assertEqual(target.read_bytes(), old_bytes)
        self.assertEqual(json.loads(receipt_path.read_text()), receipt_before)
        self.assertFalse(journal.exists())
        self.assertFalse(recovery.parent.exists())

    def test_read_only_modes_report_pending_recovery_without_changing_it(self):
        self.apply()
        receipt_path = self.consumer / ".departurepixelzh-sync.json"
        receipt_before = json.loads(receipt_path.read_text())
        target = self.consumer / "Licenses" / "NOTICE.md"
        old_bytes, old_sha256 = target.read_bytes(), digest(target)
        new_bytes = b"interrupted replacement\n"
        new_sha256 = hashlib.sha256(new_bytes).hexdigest()
        target.write_bytes(new_bytes)
        recovery = self.consumer / ".departurepixelzh-sync-recovery" / "Licenses"
        recovery.mkdir(parents=True)
        (recovery / "NOTICE.md").write_bytes(old_bytes)
        journal = self.consumer / ".departurepixelzh-sync-journal.json"
        journal.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "manifest_id": "sample-consumer",
                    "receipt_before": receipt_before,
                    "receipt_after": receipt_before,
                    "changed": {"Licenses/NOTICE.md": {"before": old_sha256, "after": new_sha256}},
                    "retired": {},
                }
            )
        )
        before = {
            path.relative_to(self.consumer): (path.read_bytes(), path.stat().st_mtime_ns)
            for path in self.consumer.rglob("*")
            if path.is_file()
        }

        for mode in ({"dry_run": True}, {"check": True}):
            with self.subTest(mode=mode), self.assertRaisesRegex(ValueError, "recovery is pending"):
                self.apply(**mode)
            after = {
                path.relative_to(self.consumer): (path.read_bytes(), path.stat().st_mtime_ns)
                for path in self.consumer.rglob("*")
                if path.is_file()
            }
            self.assertEqual(after, before)

    def test_normal_sync_discards_an_orphaned_recovery_root(self):
        self.apply()
        recovery = self.consumer / ".departurepixelzh-sync-recovery"
        recovery.mkdir()
        (recovery / "staged-backup").write_bytes(b"orphaned recovery data")

        result = self.apply()

        self.assertEqual(result["changed"], [])
        self.assertFalse(recovery.exists())


if __name__ == "__main__":
    unittest.main()
