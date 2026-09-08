"""Font geometry and OpenType merging; independent of CLI, downloads, and installation."""

import unicodedata

from fontTools.fontBuilder import FontBuilder
from fontTools.merge import Merger
from fontTools.misc.transform import Transform
from fontTools.pens.boundsPen import ControlBoundsPen
from fontTools.pens.cu2quPen import Cu2QuPen
from fontTools.pens.recordingPen import DecomposingRecordingPen
from fontTools.pens.transformPen import TransformPen
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import newTable
from fontTools.ttLib.scaleUpem import scale_upem
from fontTools.ttLib.tables import otTables


def bounds(glyph_set, name):
    pen = ControlBoundsPen(glyph_set)
    glyph_set[name].draw(pen)
    return pen.bounds


def rebuild_outlines(font, transforms, max_error):
    """Flatten components before transforming, preserving glyph names for layout tables."""
    glyph_set = font.getGlyphSet()
    glyphs, metrics = {}, {}
    for name in font.getGlyphOrder():
        transform, advance = transforms[name]
        recording = DecomposingRecordingPen(glyph_set)
        glyph_set[name].draw(recording)
        pen = TTGlyphPen(None)
        recording.replay(
            TransformPen(
                Cu2QuPen(pen, max_err=max_error, reverse_direction="CFF " in font), transform
            )
        )
        glyph = pen.glyph()
        glyphs[name] = glyph
        glyph.recalcBounds(None)
        metrics[name] = (advance, getattr(glyph, "xMin", 0))
    if "CFF " in font:
        del font["CFF "]
    for tag in ("DSIG", "FFTM", "PfEd", "cvt ", "fpgm", "prep", "VORG"):
        if tag in font:
            del font[tag]
    builder = FontBuilder(font=font)
    builder.isTTF = True
    font.sfntVersion = "\x00\x01\x00\x00"
    builder.setupGlyf(glyphs)
    builder.setupHorizontalMetrics(metrics)
    builder.setupMaxp()


def transform_anchor(anchor, transform):
    if anchor is not None:
        x, y = anchor.XCoordinate, anchor.YCoordinate
        anchor.XCoordinate = round(x * transform.xx + y * transform.yx + transform.dx)
        anchor.YCoordinate = round(x * transform.xy + y * transform.yy + transform.dy)


def transform_gpos_anchors(font, transforms):
    """Keep source anchor attachments aligned with transformed outlines.

    Fixed-cell fitting only needs anchor-positioning lookups. Rejecting other
    GPOS lookups is safer than shipping a compact face with subtly stale layout.
    """
    if "GPOS" not in font or not any(transform != Transform() for transform in transforms.values()):
        return
    for lookup in font["GPOS"].table.LookupList.Lookup:
        lookup_type = lookup.LookupType
        subtables = lookup.SubTable
        if lookup_type == 9:
            lookup_type = None
            expanded = []
            for table in subtables:
                if lookup_type is None:
                    lookup_type = table.ExtensionLookupType
                elif lookup_type != table.ExtensionLookupType:
                    raise ValueError("GPOS extension lookup has mixed types")
                expanded.append(table.ExtSubTable)
            subtables = expanded
        if lookup_type not in (3, 4, 5, 6):
            raise ValueError(f"Cannot translate GPOS lookup type {lookup_type}")
        for table in subtables:
            if lookup_type == 3:
                for name, record in zip(table.Coverage.glyphs, table.EntryExitRecord):
                    transform_anchor(record.EntryAnchor, transforms[name])
                    transform_anchor(record.ExitAnchor, transforms[name])
            elif lookup_type == 4:
                for name, record in zip(table.MarkCoverage.glyphs, table.MarkArray.MarkRecord):
                    transform_anchor(record.MarkAnchor, transforms[name])
                for name, record in zip(table.BaseCoverage.glyphs, table.BaseArray.BaseRecord):
                    for anchor in record.BaseAnchor:
                        transform_anchor(anchor, transforms[name])
            elif lookup_type == 5:
                for name, record in zip(table.MarkCoverage.glyphs, table.MarkArray.MarkRecord):
                    transform_anchor(record.MarkAnchor, transforms[name])
                for name, attach in zip(
                    table.LigatureCoverage.glyphs, table.LigatureArray.LigatureAttach
                ):
                    for component in attach.ComponentRecord:
                        for anchor in component.LigatureAnchor:
                            transform_anchor(anchor, transforms[name])
            else:
                for name, record in zip(table.Mark1Coverage.glyphs, table.Mark1Array.MarkRecord):
                    transform_anchor(record.MarkAnchor, transforms[name])
                for name, record in zip(table.Mark2Coverage.glyphs, table.Mark2Array.Mark2Record):
                    for anchor in record.Mark2Anchor:
                        transform_anchor(anchor, transforms[name])


def prepare(font, source, metrics, source_cell):
    mode = source["mode"]
    cell, ascent, descent = metrics["cell"], metrics["ascent"], metrics["descent"]
    font["head"].unitsPerEm = source["design_units_per_em"] or font["head"].unitsPerEm
    scale_upem(font, metrics["units_per_em"])
    if mode == "mono_fit" and "GPOS" in font:
        # Proportional positioning is incompatible with fixed-cell fitting.
        del font["GPOS"]
    cmap = font.getBestCmap()
    by_name = {}
    for cp, name in cmap.items():
        by_name.setdefault(name, []).append(cp)
    glyph_set = font.getGlyphSet()
    transforms = {}
    for name in font.getGlyphOrder():
        width = font["hmtx"].metrics[name][0]
        box = bounds(glyph_set, name)
        cps = by_name.get(name, [])
        if source["box_tile"] and any(0x2500 <= cp <= 0x259F for cp in cps):
            # Stretch the source tile, not each stroke, so adjacent cells still join.
            tile = source["box_tile"]
            sx, sy = cell / tile["width"], (ascent - descent) / tile["height"]
            transforms[name] = (
                Transform(sx, 0, 0, sy, -tile["left"] * sx, descent - tile["bottom"] * sy),
                cell,
            )
            continue
        if mode == "preserve":
            if width == 0:
                advance = 0
            elif width == source_cell:
                advance = cell
            elif width == source_cell * 2:
                advance = cell * 2
            else:
                raise ValueError(
                    f"Preserved glyph {name} has advance {width}; expected {source_cell} or "
                    f"{source_cell * 2}"
                )
            if box is None or advance == 0:
                transform = Transform()
            else:
                x0, _, x1, _ = box
                bounds_width = x1 - x0
                if bounds_width > advance:
                    scale = advance / bounds_width
                    transform = Transform(scale, 0, 0, 1, -x0 * scale, 0)
                else:
                    centered = (advance - width) / 2
                    shift = min(max(centered, -x0), advance - x1)
                    transform = Transform(1, 0, 0, 1, shift, 0)
            transforms[name] = (transform, advance)
            continue
        full = mode == "mono_fit" and (
            any(unicodedata.east_asian_width(chr(cp)) in "WF" for cp in cps)
            if cps
            else width >= source["fullwidth_threshold"]
        )
        advance = 0 if width == 0 else cell * (2 if full else 1)
        transform = Transform()
        if box and advance:
            x0, y0, x1, y1 = box
            if mode == "icons":
                separator = any(0xE0B0 <= cp <= 0xE0D4 for cp in cps)
                if separator:
                    sx = advance / max(x1 - x0, 1)
                    sy = (ascent - descent) / max(y1 - y0, 1)
                    transform = Transform(sx, 0, 0, sy, -x0 * sx, descent - y0 * sy)
                else:
                    scale = min(
                        (advance - metrics["horizontal_padding"]) / max(x1 - x0, 1),
                        metrics["icon_height"] / max(y1 - y0, 1),
                    )
                    transform = Transform(
                        scale,
                        0,
                        0,
                        scale,
                        (advance - (x0 + x1) * scale) / 2,
                        metrics["icon_center"] - (y0 + y1) * scale / 2,
                    )
            else:
                sx = min(1, (advance - metrics["horizontal_padding"]) / max(x1 - x0, 1))
                transform = Transform(sx, 0, 0, 1, (advance - (x0 + x1) * sx) / 2, 0)
        transforms[name] = (transform, advance)
    for name, cps in by_name.items():
        if any(
            0x2E80 <= cp <= 0x303F
            or 0x3100 <= cp <= 0x31BF
            or 0x3400 <= cp <= 0x9FFF
            or 0xF900 <= cp <= 0xFAFF
            or 0xFE10 <= cp <= 0xFE1F
            or 0xFE30 <= cp <= 0xFE6F
            or 0xFF01 <= cp <= 0xFF60
            or 0x20000 <= cp <= 0x323AF
            for cp in cps
        ):
            transform, advance = transforms[name]
            scale = metrics["cjk_scale"]
            # Reduce ink around the CJK em center without changing terminal cells.
            center_y = metrics["units_per_em"] / 2 + metrics["descent"] / 2
            optical = Transform(
                scale, 0, 0, scale, advance / 2 * (1 - scale), center_y * (1 - scale)
            )
            transforms[name] = (optical.transform(transform), advance)
    transform_gpos_anchors(font, {name: transform for name, (transform, _) in transforms.items()})
    rebuild_outlines(font, transforms, metrics["outline_error"])
    return font


def private_use(cp):
    return 0xE000 <= cp <= 0xF8FF or 0xF0000 <= cp <= 0xFFFFD or 0x100000 <= cp <= 0x10FFFD


def supplement_marks(font, metrics):
    """Keep working source anchors; attach only the base/mark pairs absent after merging."""
    cmap, glyf = font.getBestCmap(), font["glyf"]
    if not any(
        0x300 <= cp <= 0x36F and unicodedata.combining(chr(cp)) in (202, 220, 230) for cp in cmap
    ):
        return
    if "GPOS" not in font:
        font["GPOS"] = newTable("GPOS")
        font["GPOS"].table = otTables.GPOS()
        table = font["GPOS"].table
        table.Version = 0x00010000
        table.ScriptList, table.FeatureList, table.LookupList = (
            otTables.ScriptList(),
            otTables.FeatureList(),
            otTables.LookupList(),
        )
        table.ScriptList.ScriptRecord, table.ScriptList.ScriptCount = [], 0
        table.FeatureList.FeatureRecord, table.FeatureList.FeatureCount = [], 0
        table.LookupList.Lookup, table.LookupList.LookupCount = [], 0
    gpos = font["GPOS"].table
    if not any(record.FeatureTag == "mark" for record in gpos.FeatureList.FeatureRecord):
        feature = otTables.FeatureRecord()
        feature.FeatureTag, feature.Feature = "mark", otTables.Feature()
        (
            feature.Feature.FeatureParams,
            feature.Feature.LookupListIndex,
            feature.Feature.LookupCount,
        ) = None, [], 0
        feature_index = len(gpos.FeatureList.FeatureRecord)
        gpos.FeatureList.FeatureRecord.append(feature)
        gpos.FeatureList.FeatureCount = feature_index + 1
        if not gpos.ScriptList.ScriptRecord:
            script = otTables.ScriptRecord()
            script.ScriptTag, script.Script = "DFLT", otTables.Script()
            (
                script.Script.DefaultLangSys,
                script.Script.LangSysRecord,
                script.Script.LangSysCount,
            ) = None, [], 0
            gpos.ScriptList.ScriptRecord, gpos.ScriptList.ScriptCount = [script], 1
        for record in gpos.ScriptList.ScriptRecord:
            if record.Script.DefaultLangSys is None:
                lang = otTables.LangSys()
                lang.LookupOrder, lang.ReqFeatureIndex, lang.FeatureIndex, lang.FeatureCount = (
                    None,
                    0xFFFF,
                    [],
                    0,
                )
                record.Script.DefaultLangSys = lang
            for lang in [record.Script.DefaultLangSys] + [
                entry.LangSys for entry in record.Script.LangSysRecord
            ]:
                lang.FeatureIndex.append(feature_index)
                lang.FeatureCount = len(lang.FeatureIndex)
    covered = set()
    existing_x = {}
    for lookup in gpos.LookupList.Lookup:
        if lookup.LookupType != 4:
            continue
        for table in lookup.SubTable:
            for mark_name, mark in zip(table.MarkCoverage.glyphs, table.MarkArray.MarkRecord):
                existing_x.setdefault(mark_name, mark.MarkAnchor.XCoordinate)
                for base_name, base in zip(table.BaseCoverage.glyphs, table.BaseArray.BaseRecord):
                    if base.BaseAnchor[mark.Class] is not None:
                        covered.add((base_name, mark_name))
    marks = sorted(
        {
            name: unicodedata.combining(chr(cp))
            for cp, name in cmap.items()
            if 0x300 <= cp <= 0x36F and unicodedata.combining(chr(cp)) in (202, 220, 230)
        }.items(),
        key=lambda item: font.getGlyphID(item[0]),
    )
    bases = sorted(
        {
            name
            for cp, name in cmap.items()
            if cp < 0x3000 and unicodedata.category(chr(cp)).startswith("L")
        },
        key=font.getGlyphID,
    )

    def anchor(x, y):
        result = otTables.Anchor()
        result.Format, result.XCoordinate, result.YCoordinate = 1, round(x), round(y)
        return result

    table = otTables.MarkBasePos()
    table.Format, table.ClassCount = 1, len(marks)
    table.MarkCoverage, table.BaseCoverage = otTables.Coverage(), otTables.Coverage()
    table.MarkCoverage.glyphs = [name for name, _ in marks]
    table.BaseCoverage.glyphs = bases
    table.MarkArray, table.BaseArray = otTables.MarkArray(), otTables.BaseArray()
    table.MarkArray.MarkRecord, table.BaseArray.BaseRecord = [], []
    for index, (name, combining) in enumerate(marks):
        glyph = glyf[name]
        mark = otTables.MarkRecord()
        mark.Class = index
        mark.MarkAnchor = anchor(
            existing_x.get(name, (glyph.xMin + glyph.xMax) / 2),
            glyph.yMin if combining == 230 else glyph.yMax,
        )
        table.MarkArray.MarkRecord.append(mark)
    for base_name in bases:
        glyph = glyf[base_name]
        base = otTables.BaseRecord()
        base.BaseAnchor = [
            None
            if (base_name, name) in covered
            else anchor(
                metrics["cell"] / 2,
                glyph.yMax + metrics["mark_gap"]
                if combining == 230
                else glyph.yMin - metrics["mark_gap"],
            )
            for name, combining in marks
        ]
        table.BaseArray.BaseRecord.append(base)
    table.MarkArray.MarkCount, table.BaseArray.BaseCount = len(marks), len(bases)
    lookup = otTables.Lookup()
    lookup.LookupType, lookup.LookupFlag, lookup.SubTableCount, lookup.SubTable = 4, 0, 1, [table]
    index = len(gpos.LookupList.Lookup)
    gpos.LookupList.Lookup.append(lookup)
    gpos.LookupList.LookupCount = index + 1
    for record in gpos.FeatureList.FeatureRecord:
        if record.FeatureTag == "mark":
            record.Feature.LookupListIndex.append(index)
            record.Feature.LookupCount = len(record.Feature.LookupListIndex)
    if "GDEF" not in font:
        font["GDEF"] = newTable("GDEF")
        font["GDEF"].table = otTables.GDEF()
        font["GDEF"].table.Version = 0x00010000
        for attribute in ("GlyphClassDef", "AttachList", "LigCaretList", "MarkAttachClassDef"):
            setattr(font["GDEF"].table, attribute, None)
    if font["GDEF"].table.GlyphClassDef is None:
        font["GDEF"].table.GlyphClassDef = otTables.ClassDef()
        font["GDEF"].table.GlyphClassDef.classDefs = {}
    classes = font["GDEF"].table.GlyphClassDef.classDefs
    for name, _ in marks:
        classes[name] = 3
    for name in bases:
        classes.setdefault(name, 1)


def merge_fonts(paths, recipe, family, postscript_name=None):
    font = Merger().merge(paths)
    if recipe["supplement_latin_marks"]:
        supplement_marks(font, recipe["metrics"])
    metrics = recipe["metrics"]
    ascent, descent, cell = metrics["ascent"], metrics["descent"], metrics["cell"]
    font.recalcTimestamp = False
    font["head"].created = font["head"].modified = recipe["timestamp"]
    font["head"].fontRevision = recipe["font_revision"]
    font["hhea"].ascent, font["hhea"].descent, font["hhea"].lineGap = (
        ascent,
        descent,
        metrics["line_gap"],
    )
    os2 = font["OS/2"]
    os2.version = max(4, os2.version)
    os2.sTypoAscender, os2.sTypoDescender, os2.sTypoLineGap = ascent, descent, metrics["line_gap"]
    os2.usWinAscent, os2.usWinDescent = ascent, -descent
    os2.fsType = 0
    os2.fsSelection = 0xC0  # Regular + USE_TYPO_METRICS.
    os2.usWeightClass, os2.usWidthClass = 400, 5
    os2.sxHeight, os2.sCapHeight = metrics["x_height"], metrics["cap_height"]
    os2.xAvgCharWidth = cell
    os2.panose.bProportion = 9
    font["post"].isFixedPitch = 1
    font["post"].italicAngle = 0
    font["post"].underlinePosition, font["post"].underlineThickness = (
        metrics["underline_position"],
        metrics["underline_thickness"],
    )
    font["name"].names = []
    postscript_name = postscript_name or family
    name_values = {
        0: recipe["copyright"],
        1: family,
        2: "Regular",
        3: f"{recipe['version_label']};{recipe['vendor']};{postscript_name}-Regular",
        4: family,
        5: f"Version {recipe['version_label']}",
        6: f"{postscript_name}-Regular",
        16: family,
        17: "Regular",
        13: recipe["license_description"],
        14: recipe["license_url"],
    }
    for name_id, text in name_values.items():
        font["name"].setName(text, name_id, 3, 1, 0x409)
        font["name"].setName(text, name_id, 1, 0, 0)
    font["gasp"] = newTable("gasp")
    font["gasp"].gaspRange = {65535: 0x000A}
    return font
