"""Installation ownership and rollback contracts; all targets are temporary."""

import json
import os
import shutil
import unittest
from pathlib import Path
from unittest.mock import patch

from departure_pixel_zh_builder import build, install
from departure_pixel_zh_builder.build import digest, identity
from tests import test_pipeline

install.run_build = test_pipeline.build_development


class InstallTests(unittest.TestCase):
    def setUp(self):
        test_pipeline.PipelineTests.setUp(self)
        self.destination = self.root / "Fonts"
        self.receipt = self.root / "State" / "receipt.json"

    def apply(self):
        return install.install_fonts(self.preset, self.output, self.destination, self.receipt)

    def revise(self):
        recipe = json.loads(self.preset.read_text())
        recipe["families"][0]["order"] = ["two", "one"]
        self.preset.write_text(json.dumps(recipe))

    def rename_to_four(self):
        recipe = json.loads(self.preset.read_text())
        recipe["families"] = [
            {"name": "NewOne", "order": ["one", "two"]},
            {"name": "NewTwo", "order": ["two", "one"]},
            {"name": "NewThree", "order": ["one", "two"]},
            {"name": "NewFour", "order": ["two", "one"]},
        ]
        self.preset.write_text(json.dumps(recipe))

    def snapshot(self):
        return {
            p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.destination.glob("*.ttf")
        }

    def test_idempotent_adoption(self):
        build.run_build(self.preset, self.output, formats=("ttf",))
        self.destination.mkdir()
        for path in self.output.glob("*.ttf"):
            shutil.copy2(path, self.destination / path.name)
        before = self.snapshot()
        first = self.apply()
        self.assertEqual(first["changed"], [])
        self.assertEqual(before, self.snapshot())
        stamp = self.receipt.stat().st_mtime_ns
        second = self.apply()
        self.assertEqual(second["changed"], [])
        self.assertEqual(stamp, self.receipt.stat().st_mtime_ns)
        self.assertEqual(before, self.snapshot())
        checked = install.check_install(self.preset, self.destination, self.receipt)
        self.assertEqual(len(checked["checked"]), 2)

    def test_conflict_preflight_preserves_first_target(self):
        self.destination.mkdir()
        conflict = self.destination / "TwoFirst-Regular.ttf"
        conflict.write_bytes(b"unknown font")
        with self.assertRaisesRegex(ValueError, "unmanaged"):
            self.apply()
        self.assertFalse((self.destination / "OneFirst-Regular.ttf").exists())
        self.assertEqual(conflict.read_bytes(), b"unknown font")
        self.assertFalse(self.receipt.exists())

    def test_output_overlap_is_rejected_before_a_build_can_mutate_installation_state(self):
        output = self.destination
        output.mkdir()
        target = output / "OneFirst-Regular.ttf"
        target.write_bytes(b"managed target")
        self.receipt.parent.mkdir()
        self.receipt.write_bytes(b"receipt")
        journal = self.receipt.with_name(self.receipt.name + ".install-journal.json")
        journal.write_bytes(b"journal")
        backup = self.receipt.parent / "Backups" / "checksum" / target.name
        backup.parent.mkdir(parents=True)
        backup.write_bytes(b"backup")
        before = {
            path.relative_to(self.root): (path.read_bytes(), path.stat().st_mtime_ns)
            for path in self.root.rglob("*")
            if path.is_file()
        }

        with (
            patch.object(install, "run_build", side_effect=AssertionError("must not build")),
            self.assertRaisesRegex(ValueError, "disjoint"),
        ):
            install.install_fonts(self.preset, output, self.destination, self.receipt)

        after = {
            path.relative_to(self.root): (path.read_bytes(), path.stat().st_mtime_ns)
            for path in self.root.rglob("*")
            if path.is_file()
        }
        self.assertEqual(after, before)

    def test_output_ancestor_and_descendant_overlaps_are_rejected_before_building(self):
        cases = {
            "ancestor": self.root,
            "installation descendant": self.destination / "Build",
            "receipt-state descendant": self.receipt.parent / "Build",
        }
        for label, output in cases.items():
            with (
                self.subTest(label=label),
                patch.object(install, "run_build", side_effect=AssertionError("must not build")),
                self.assertRaisesRegex(ValueError, "disjoint"),
            ):
                install.install_fonts(self.preset, output, self.destination, self.receipt)

    def test_managed_update_keeps_backup(self):
        self.apply()
        before = self.snapshot()
        self.revise()
        result = self.apply()
        self.assertEqual(result["changed"], ["OneFirst-Regular.ttf"])
        self.assertEqual(before["TwoFirst-Regular.ttf"], self.snapshot()["TwoFirst-Regular.ttf"])
        backups = list((self.receipt.parent / "Backups").glob("*/*.ttf"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), before["OneFirst-Regular.ttf"][0])
        install.check_install(self.preset, self.destination, self.receipt)

    def test_modified_managed_target_rejected(self):
        self.apply()
        (self.destination / "TwoFirst-Regular.ttf").write_bytes(b"user edit")
        before, receipt = self.snapshot(), self.receipt.read_bytes()
        self.revise()
        with self.assertRaisesRegex(ValueError, "modified"):
            self.apply()
        self.assertEqual(before, self.snapshot())
        self.assertEqual(receipt, self.receipt.read_bytes())

    def test_missing_installed_file_fails_check_without_build(self):
        self.apply()
        (self.destination / "OneFirst-Regular.ttf").unlink()
        with (
            patch.object(install, "run_build", side_effect=AssertionError("must not build")),
            self.assertRaisesRegex(ValueError, "missing"),
        ):
            install.check_install(self.preset, self.destination, self.receipt)

    def test_check_needs_no_source_or_output(self):
        self.apply()
        shutil.rmtree(self.output)
        (self.root / "one.ttf").unlink()
        (self.root / "two.ttf").unlink()
        with patch.object(install, "run_build", side_effect=AssertionError("must not build")):
            install.check_install(self.preset, self.destination, self.receipt)

    def test_receipt_failure_rolls_back_update(self):
        self.apply()
        before, receipt = self.snapshot(), self.receipt.read_bytes()
        self.revise()
        original = install.write_json

        def fail_after_write(path, value):
            original(path, value)
            raise OSError("injected receipt failure")

        with (
            patch.object(install, "write_json", side_effect=fail_after_write),
            self.assertRaisesRegex(OSError, "injected"),
        ):
            self.apply()
        self.assertEqual(before, self.snapshot())
        self.assertEqual(receipt, self.receipt.read_bytes())

    def test_incomplete_journal_is_restored_before_the_next_install(self):
        self.apply()
        old_receipt = json.loads(self.receipt.read_text())
        old_font = self.destination / "OneFirst-Regular.ttf"
        old_bytes = old_font.read_bytes()
        old_sha256 = digest(old_font)
        self.revise()
        build.run_build(self.preset, self.output, formats=("ttf",))
        new_sha256 = digest(self.output / "OneFirst-Regular.ttf")
        backup = self.receipt.parent / "Backups" / old_sha256 / old_font.name
        backup.parent.mkdir(parents=True)
        shutil.copy2(old_font, backup)
        shutil.copy2(self.output / old_font.name, old_font)
        new_receipt = {
            **old_receipt,
            "preset_fingerprint": identity(json.loads(self.preset.read_text())),
        }
        new_receipt["files"] = {**old_receipt["files"]}
        new_receipt["files"][old_font.name] = {
            **old_receipt["files"][old_font.name],
            "sha256": new_sha256,
        }
        journal = self.receipt.with_name(self.receipt.name + ".install-journal.json")
        journal.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "destination": str(self.destination.resolve()),
                    "receipt_before": old_receipt,
                    "receipt_after": new_receipt,
                    "changed": {old_font.name: {"before": old_sha256, "after": new_sha256}},
                    "retired": {},
                }
            )
        )

        self.assertTrue(install.recover_pending(self.destination, self.receipt))

        self.assertEqual(old_font.read_bytes(), old_bytes)
        self.assertEqual(json.loads(self.receipt.read_text()), old_receipt)
        self.assertFalse(journal.exists())

    def test_font_write_failure_rolls_back_new_files(self):
        original = os.replace
        count = 0

        def fail_second_font(source, target):
            nonlocal count
            if Path(target).parent == self.destination.resolve():
                count += 1
                if count == 2:
                    raise OSError("injected font failure")
            return original(source, target)

        with (
            patch.object(install.os, "replace", side_effect=fail_second_font),
            self.assertRaisesRegex(OSError, "injected"),
        ):
            self.apply()
        self.assertEqual(self.snapshot(), {})
        self.assertFalse(self.receipt.exists())

    def test_symlink_target_rejected(self):
        self.destination.mkdir()
        other = self.root / "other.ttf"
        other.write_bytes(b"untouched")
        (self.destination / "TwoFirst-Regular.ttf").symlink_to(other)
        with self.assertRaisesRegex(ValueError, "symbolic"):
            self.apply()
        self.assertFalse((self.destination / "OneFirst-Regular.ttf").exists())
        self.assertEqual(other.read_bytes(), b"untouched")

    def test_changed_preset_check_fails(self):
        self.apply()
        self.revise()
        with self.assertRaisesRegex(ValueError, "current preset"):
            install.check_install(self.preset, self.destination, self.receipt)

    def test_rename_retires_previous_receipt_files_and_keeps_unknown_fonts(self):
        self.apply()
        user_font = self.destination / "KeptByUser-Regular.ttf"
        user_font.write_bytes(b"user font")
        self.rename_to_four()

        result = self.apply()

        self.assertEqual(
            result["changed"],
            [
                "NewOne-Regular.ttf",
                "NewTwo-Regular.ttf",
                "NewThree-Regular.ttf",
                "NewFour-Regular.ttf",
            ],
        )
        self.assertEqual(result["removed"], ["OneFirst-Regular.ttf", "TwoFirst-Regular.ttf"])
        self.assertFalse((self.destination / "OneFirst-Regular.ttf").exists())
        self.assertFalse((self.destination / "TwoFirst-Regular.ttf").exists())
        self.assertEqual(user_font.read_bytes(), b"user font")
        self.assertEqual(
            set(json.loads(self.receipt.read_text())["files"]),
            {
                "NewOne-Regular.ttf",
                "NewTwo-Regular.ttf",
                "NewThree-Regular.ttf",
                "NewFour-Regular.ttf",
            },
        )
        install.check_install(self.preset, self.destination, self.receipt)

    def test_missing_retired_font_is_absent_without_a_backup_or_removal(self):
        self.apply()
        (self.destination / "OneFirst-Regular.ttf").unlink()
        self.rename_to_four()

        result = self.apply()

        self.assertEqual(result["removed"], ["TwoFirst-Regular.ttf"])
        self.assertFalse((self.destination / "OneFirst-Regular.ttf").exists())
        self.assertFalse((self.destination / "TwoFirst-Regular.ttf").exists())
        self.assertEqual(
            {path.name for path in (self.receipt.parent / "Backups").glob("*/*.ttf")},
            {"TwoFirst-Regular.ttf"},
        )
        install.check_install(self.preset, self.destination, self.receipt)

    def test_modified_retired_font_rejects_rename_before_writes(self):
        self.apply()
        retired = self.destination / "OneFirst-Regular.ttf"
        retired.write_bytes(b"user edit")
        before, receipt = self.snapshot(), self.receipt.read_bytes()
        self.rename_to_four()

        with self.assertRaisesRegex(ValueError, "modified"):
            self.apply()

        self.assertEqual(before, self.snapshot())
        self.assertEqual(receipt, self.receipt.read_bytes())
        self.assertFalse((self.receipt.parent / "Backups").exists())

    def test_symlinked_retired_font_rejects_rename_before_writes(self):
        self.apply()
        retired = self.destination / "OneFirst-Regular.ttf"
        other = self.root / "other.ttf"
        other.write_bytes(b"untouched")
        retired.unlink()
        retired.symlink_to(other)
        receipt = self.receipt.read_bytes()
        self.rename_to_four()

        with self.assertRaisesRegex(ValueError, "symbolic"):
            self.apply()

        self.assertEqual(other.read_bytes(), b"untouched")
        self.assertEqual(receipt, self.receipt.read_bytes())
        self.assertFalse((self.destination / "NewOne-Regular.ttf").exists())
        self.assertFalse((self.receipt.parent / "Backups").exists())

    def test_receipt_failure_rolls_back_renamed_and_retired_fonts(self):
        self.apply()
        before, receipt = self.snapshot(), self.receipt.read_bytes()
        self.rename_to_four()
        original = install.write_json

        def fail_after_write(path, value):
            original(path, value)
            raise OSError("injected receipt failure")

        with (
            patch.object(install, "write_json", side_effect=fail_after_write),
            self.assertRaisesRegex(OSError, "injected"),
        ):
            self.apply()

        self.assertEqual(before, self.snapshot())
        self.assertEqual(receipt, self.receipt.read_bytes())

    def test_retire_removal_failure_rolls_back_renamed_and_retired_fonts(self):
        self.apply()
        before, receipt = self.snapshot(), self.receipt.read_bytes()
        self.rename_to_four()
        original = os.unlink
        retired_removals = 0

        def fail_second_retirement(path, *args, **kwargs):
            nonlocal retired_removals
            target = Path(path)
            if target.parent == self.destination.resolve() and target.name in {
                "OneFirst-Regular.ttf",
                "TwoFirst-Regular.ttf",
            }:
                retired_removals += 1
                if retired_removals == 2:
                    raise OSError("injected retirement failure")
            return original(path, *args, **kwargs)

        with (
            patch.object(install.os, "unlink", side_effect=fail_second_retirement),
            self.assertRaisesRegex(OSError, "injected retirement failure"),
        ):
            self.apply()

        self.assertEqual(before, self.snapshot())
        self.assertEqual(receipt, self.receipt.read_bytes())


if __name__ == "__main__":
    unittest.main()
