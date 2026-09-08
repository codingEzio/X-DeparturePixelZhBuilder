"""Observable build/cache/install boundaries using tiny, independently generated fonts."""

import hashlib
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont

from departure_pixel_zh_builder import build
from departure_pixel_zh_builder.check import check_build, verify_web_font
from departure_pixel_zh_builder.font_merge import supplement_marks

_run_build = build.run_build


def build_development(*args, **kwargs):
    """Fixtures intentionally exercise the explicit unpinned development path."""
    kwargs["development"] = True
    return _run_build(*args, **kwargs)


build.run_build = build_development


def fixture(
    path,
    family,
    characters,
    inset,
    double_width=(),
    zero_width=(),
    glyph_bounds=None,
    add_marks=False,
):
    builder = FontBuilder(1000, isTTF=True)
    order = [".notdef"] + [f"u{cp:X}" for cp in characters]
    builder.setupGlyphOrder(order)
    builder.setupCharacterMap({cp: f"u{cp:X}" for cp in characters})
    glyph_bounds = glyph_bounds or {}
    glyphs = {}
    for name, cp in [(".notdef", None)] + [(f"u{cp:X}", cp) for cp in characters]:
        left, bottom, right, top = glyph_bounds.get(cp, (inset, 0, 450, 700))
        pen = TTGlyphPen(None)
        pen.moveTo((left, bottom))
        pen.lineTo((right, bottom))
        pen.lineTo((right, top))
        pen.lineTo((left, top))
        pen.closePath()
        glyphs[name] = pen.glyph()
    builder.setupGlyf(glyphs)
    builder.setupHorizontalMetrics(
        {
            name: (
                0 if cp in zero_width else 1000 if cp in double_width else 500,
                glyph_bounds.get(cp, (inset, 0, 450, 700))[0],
            )
            for name, cp in [(".notdef", None)] + [(f"u{cp:X}", cp) for cp in characters]
        }
    )
    builder.setupHorizontalHeader(ascent=900, descent=-200)
    builder.setupNameTable(
        {
            "familyName": family,
            "styleName": "Regular",
            "uniqueFontIdentifier": family,
            "fullName": family,
            "psName": family,
        }
    )
    builder.setupOS2(sTypoAscender=900, sTypoDescender=-200, usWinAscent=900, usWinDescent=200)
    builder.setupPost()
    builder.setupMaxp()
    builder.save(path)
    if add_marks:
        font = TTFont(path)
        supplement_marks(font, {"cell": 500, "mark_gap": 100})
        font.save(path)


def mark_anchors(font, base_name, mark_name):
    for lookup in font["GPOS"].table.LookupList.Lookup:
        if lookup.LookupType != 4:
            continue
        for table in lookup.SubTable:
            if (
                mark_name not in table.MarkCoverage.glyphs
                or base_name not in table.BaseCoverage.glyphs
            ):
                continue
            mark_index = table.MarkCoverage.glyphs.index(mark_name)
            base_index = table.BaseCoverage.glyphs.index(base_name)
            mark = table.MarkArray.MarkRecord[mark_index]
            base = table.BaseArray.BaseRecord[base_index].BaseAnchor[mark.Class]
            if base is not None:
                return base.XCoordinate, mark.MarkAnchor.XCoordinate
    raise AssertionError("missing mark anchor pair")


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        fixture(
            self.root / "one.ttf",
            "SourceOne",
            [35, 65, 66, 123, 125, 0x301, 0x4E00],
            50,
            double_width=[0x4E00],
            glyph_bounds={
                35: (0, 0, 500, 700),
                123: (0, 0, 400, 700),
                125: (100, 0, 500, 700),
                0x301: (50, 0, 450, 100),
            },
            add_marks=True,
        )
        fixture(self.root / "two.ttf", "SourceTwo", [65, 67], 150)
        recipe = json.loads((Path(__file__).parent / "fixtures" / "base_recipe.json").read_text())
        recipe.update(id="fixture", documents=[], license_directory=None, private_use_source=None)
        recipe["validation"] = {
            "single_cell": "ABC#{}",
            "double_cell": "一",
            "combining": ["Á"],
            "icons": [],
        }
        recipe["sources"] = [
            {
                "id": key,
                "location": f"{key}.ttf",
                "sha256": hashlib.sha256((self.root / f"{key}.ttf").read_bytes()).hexdigest(),
                "member": None,
                "mode": "preserve",
                "design_units_per_em": 1100,
                "fullwidth_threshold": None,
                "box_tile": None,
                "reserved_names": [],
            }
            for key in ("one", "two")
        ]
        recipe["metrics"]["cell"] = 500
        recipe["families"] = [
            {"name": "OneFirst", "order": ["one", "two"]},
            {"name": "TwoFirst", "order": ["two", "one"]},
        ]
        self.preset = self.root / "recipe.json"
        self.preset.write_text(json.dumps(recipe))
        (self.root / "builder-version").write_text("development\n")
        self.output = self.root / "Output"

    def test_cjk_scale_preserves_latin_and_advances(self):
        recipe = json.loads(self.preset.read_text())
        snapshots = []
        for scale in (1, 0.95):
            recipe["metrics"]["cjk_scale"] = scale
            self.preset.write_text(json.dumps(recipe))
            build.run_build(self.preset, self.output, formats=("ttf",))
            with TTFont(self.output / "OneFirst-Regular.ttf") as font:
                snapshots.append(
                    {
                        cp: (
                            font["glyf"][name].getCoordinates(font["glyf"])[0],
                            font["hmtx"].metrics[name][0],
                        )
                        for cp, name in font.getBestCmap().items()
                    }
                )
        before, after = snapshots
        for cp in before:
            self.assertEqual(before[cp][1], after[cp][1])
            if cp != 0x4E00:
                self.assertEqual(before[cp], after[cp])
        old, new = before[0x4E00][0], after[0x4E00][0]
        for axis in (0, 1):
            old_size = max(p[axis] for p in old) - min(p[axis] for p in old)
            new_size = max(p[axis] for p in new) - min(p[axis] for p in new)
            self.assertAlmostEqual(new_size, old_size * 0.95, delta=1)

    def test_other_combo_without_layout_tables_and_priority(self):
        report = build.run_build(self.preset, self.output, formats=("ttf",))
        self.assertEqual(report["rebuilt"], ["OneFirst", "TwoFirst"])
        for family, inset in (("OneFirst", 50), ("TwoFirst", 150)):
            font = TTFont(self.output / f"{family}-Regular.ttf")
            self.assertEqual(set(font.getBestCmap()), {35, 65, 66, 67, 123, 125, 0x301, 0x4E00})
            self.assertEqual(font["glyf"][font.getBestCmap()[65]].xMin, inset)

    def test_missing_source_layout_tables_supplement_marks_and_keep_priority(self):
        for key in ("one", "two"):
            path = self.root / f"{key}.ttf"
            font = TTFont(path)
            for tag in ("GPOS", "GDEF"):
                if tag in font:
                    del font[tag]
            font.save(path)
        recipe = json.loads(self.preset.read_text())
        for source in recipe["sources"]:
            source["sha256"] = hashlib.sha256(
                (self.root / f"{source['id']}.ttf").read_bytes()
            ).hexdigest()
        self.preset.write_text(json.dumps(recipe))

        build.run_build(self.preset, self.output, formats=("ttf",))
        for family, inset in (("OneFirst", 50), ("TwoFirst", 150)):
            font = TTFont(self.output / f"{family}-Regular.ttf")
            cmap = font.getBestCmap()
            self.assertEqual(font["glyf"][cmap[65]].xMin, inset)
            self.assertEqual(mark_anchors(font, "u41", "u301"), (250, 250))

    def test_unchanged_rebuild_does_no_font_work(self):
        build.run_build(self.preset, self.output, formats=("ttf",))
        path = self.output / "OneFirst-Regular.ttf"
        before = path.stat().st_mtime_ns
        events = []
        report = build.run_build(self.preset, self.output, formats=("ttf",), progress=events.append)
        self.assertEqual(report["rebuilt"], [])
        self.assertEqual(path.stat().st_mtime_ns, before)
        self.assertFalse(any(event["stage"] == "prepare" for event in events))

    def test_corrupt_output_is_rebuilt(self):
        build.run_build(self.preset, self.output, formats=("ttf",))
        path = self.output / "OneFirst-Regular.ttf"
        expected = path.read_bytes()
        path.write_bytes(b"broken")
        report = build.run_build(self.preset, self.output, formats=("ttf",))
        self.assertEqual(report["rebuilt"], ["OneFirst"])
        self.assertEqual(path.read_bytes(), expected)

    def test_recipe_change_invalidates_outputs(self):
        build.run_build(self.preset, self.output, formats=("ttf",))
        recipe = json.loads(self.preset.read_text())
        recipe["families"][0]["order"] = ["two", "one"]
        self.preset.write_text(json.dumps(recipe))
        report = build.run_build(self.preset, self.output, formats=("ttf",))
        self.assertIn("OneFirst", report["rebuilt"])
        font = TTFont(self.output / "OneFirst-Regular.ttf")
        self.assertEqual(font["glyf"][font.getBestCmap()[65]].xMin, 150)

    def test_family_metric_override_preserves_regular_grid_and_mark_anchors(self):
        recipe = json.loads(self.preset.read_text())
        recipe["families"][1]["metrics"] = {"cell": 450, "horizontal_padding": 50}
        self.preset.write_text(json.dumps(recipe))

        report = build.run_build(self.preset, self.output, formats=("ttf",))
        variants = {variant["family"]: variant for variant in report["variants"]}
        self.assertEqual(variants["OneFirst"]["metrics"], recipe["metrics"])
        self.assertEqual(
            variants["TwoFirst"]["metrics"],
            {**recipe["metrics"], "cell": 450, "horizontal_padding": 50},
        )

        regular = TTFont(self.output / "OneFirst-Regular.ttf")
        compact = TTFont(self.output / "TwoFirst-Regular.ttf")
        for font, cell in ((regular, 500), (compact, 450)):
            cmap = font.getBestCmap()
            self.assertEqual(font["hmtx"].metrics[cmap[65]][0], cell)
            self.assertEqual(font["hmtx"].metrics[cmap[0x4E00]][0], cell * 2)
        self.assertEqual(mark_anchors(regular, "u41", "u301"), (250, 250))
        self.assertEqual(mark_anchors(compact, "u41", "u301"), (225, 225))
        compact_cmap = compact.getBestCmap()
        self.assertEqual(
            (compact["glyf"][compact_cmap[35]].xMin, compact["glyf"][compact_cmap[35]].xMax),
            (0, 450),
        )
        self.assertEqual(
            (compact["glyf"][compact_cmap[123]].xMin, compact["glyf"][compact_cmap[123]].xMax),
            (0, 400),
        )
        self.assertEqual(
            (compact["glyf"][compact_cmap[125]].xMin, compact["glyf"][compact_cmap[125]].xMax),
            (50, 450),
        )

        recipe["families"][1]["metrics"]["horizontal_padding"] = 25
        self.preset.write_text(json.dumps(recipe))
        report = build.run_build(self.preset, self.output, formats=("ttf",))
        self.assertEqual(report["rebuilt"], ["TwoFirst"])
        self.assertEqual(report["reused"], ["OneFirst"])

    def test_source_cell_change_does_not_reuse_preserved_preparation(self):
        recipe = json.loads(self.preset.read_text())
        for family, cell in zip(recipe["families"], (500, 450)):
            family["metrics"] = {"cell": cell, "horizontal_padding": 50}
        self.preset.write_text(json.dumps(recipe))
        build.run_build(self.preset, self.output, formats=("ttf",))

        recipe["metrics"]["cell"] = 501
        self.preset.write_text(json.dumps(recipe))
        with self.assertRaisesRegex(ValueError, "expected 501 or 1002"):
            build.run_build(self.preset, self.output, formats=("ttf",))

    def test_unknown_source_and_reserved_name_fail_before_build(self):
        recipe = json.loads(self.preset.read_text())
        recipe["families"][0]["order"] = ["missing", "two"]
        self.preset.write_text(json.dumps(recipe))
        with self.assertRaises(ValueError):
            build.run_build(self.preset, self.output, formats=("ttf",))
        self.assertFalse((self.output / "OneFirst-Regular.ttf").exists())

    def test_missing_local_source_does_not_silently_use_cached_copy(self):
        build.run_build(self.preset, self.output, formats=("ttf",))
        (self.root / "one.ttf").write_bytes(b"changed source")
        with self.assertRaises(ValueError):
            build.run_build(self.preset, self.output, formats=("ttf",))

    def test_check_rejects_tampered_extracted_source_provenance(self):
        build.run_build(self.preset, self.output, formats=("ttf", "woff2"))
        provenance_path = self.output / "provenance.json"
        provenance = json.loads(provenance_path.read_text())
        provenance["sources"]["one"]["font_sha256"] = "0" * 64
        provenance_path.write_text(json.dumps(provenance))
        with self.assertRaisesRegex(ValueError, "pinned source inputs"):
            check_build(self.preset, self.output)

    def test_check_rejects_coverage_tampering_even_with_updated_provenance_hash(self):
        build.run_build(self.preset, self.output, formats=("ttf", "woff2"))
        coverage_path = self.output / "OneFirst_coverage.json"
        coverage_path.write_text("{}\n")
        provenance_path = self.output / "provenance.json"
        provenance = json.loads(provenance_path.read_text())
        variant = next(item for item in provenance["variants"] if item["family"] == "OneFirst")
        variant["files"][coverage_path.name] = build.digest(coverage_path)
        provenance_path.write_text(json.dumps(provenance))
        with self.assertRaisesRegex(ValueError, "Coverage map differs"):
            check_build(self.preset, self.output)

    def test_cancellation_keeps_previous_deliverables(self):
        build.run_build(self.preset, self.output, formats=("ttf",))
        before = {p.name: p.read_bytes() for p in self.output.glob("*-Regular.ttf")}

        def cancel(event):
            if event["stage"] == "merge" and event["family"] == "TwoFirst":
                raise RuntimeError("Cancelled")

        with self.assertRaisesRegex(RuntimeError, "Cancelled"):
            build.run_build(self.preset, self.output, formats=("ttf",), progress=cancel, force=True)
        self.assertEqual(
            {p.name: p.read_bytes() for p in self.output.glob("*-Regular.ttf")}, before
        )

    def test_web_export_reuses_ttf(self):
        build.run_build(self.preset, self.output, formats=("ttf",))
        before = (self.output / "OneFirst-Regular.ttf").stat().st_mtime_ns
        events = []
        result = build.run_build(
            self.preset, self.output, formats=("ttf", "woff2"), progress=events.append
        )
        self.assertEqual(result["rebuilt"], [])
        self.assertEqual((self.output / "OneFirst-Regular.ttf").stat().st_mtime_ns, before)
        self.assertTrue((self.output / "OneFirst-Regular.woff2").is_file())
        self.assertFalse(any(event["stage"] == "prepare" for event in events))

    def test_web_outline_mismatch_is_rejected(self):
        build.run_build(self.preset, self.output, formats=("ttf", "woff2"))
        web_path = self.output / "OneFirst-Regular.woff2"
        web = TTFont(web_path)
        glyph = web.getBestCmap()[65]
        coordinates, _, _ = web["glyf"][glyph].getCoordinates(web["glyf"])
        coordinates[0] = (coordinates[0][0] + 1, coordinates[0][1])
        web["glyf"][glyph].coordinates = coordinates
        web.flavor = "woff2"
        web.save(web_path)
        with self.assertRaisesRegex(ValueError, "outline differs"):
            verify_web_font(self.output / "OneFirst-Regular.ttf", web_path, "OneFirst", "OneFirst")


if __name__ == "__main__":
    unittest.main()
