"""Read-only verification for a built DeparturePixelZh release."""

import json
import re
from collections import Counter
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

from fontTools.ttLib import TTFont

from .build import (
    builder_state,
    digest,
    family_metrics,
    identity,
    load_recipe,
    owners,
    postscript_name,
    valid_source_provenance,
    validate_font,
)


def cached_source_cmaps(recipe, sources_dir):
    """Read only the source archives already verified by provenance validation."""
    result = {}
    for source in recipe["sources"]:
        archive = sources_dir / (source["id"] + (".zip" if source["member"] else ".ttf"))
        data = archive.read_bytes()
        if source["member"]:
            with ZipFile(BytesIO(data)) as contents:
                data = contents.read(source["member"])
        result[source["id"]] = TTFont(BytesIO(data), recalcTimestamp=False).getBestCmap()
    return result


def verify_document_references(document, root):
    """Require every relative HTML/CSS asset reference to stay in the built release tree."""
    root = Path(root).resolve()
    document = Path(document).resolve()
    content = document.read_text()
    values = re.findall(
        r"(?:href|src)\s*=\s*['\"]([^'\"]+)['\"]|url\(\s*['\"]?([^'\")\s]+)", content, re.IGNORECASE
    )
    for attribute_url, css_url in values:
        url = attribute_url or css_url
        if url.startswith(("#", "data:", "http:", "https:", "mailto:")):
            continue
        target = (document.parent / url).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            raise ValueError(
                f"Document asset does not resolve in release layout: {document.relative_to(root)} -> {url}"
            )


def verify_web_font(ttf_path, woff_path, family, postscript):
    ttf, web = TTFont(ttf_path), TTFont(woff_path)
    if (
        web["name"].getDebugName(1) != family
        or web["name"].getDebugName(6) != postscript + "-Regular"
        or web.getBestCmap() != ttf.getBestCmap()
        or web["head"].unitsPerEm != ttf["head"].unitsPerEm
        or (web["hhea"].ascent, web["hhea"].descent, web["hhea"].lineGap)
        != (ttf["hhea"].ascent, ttf["hhea"].descent, ttf["hhea"].lineGap)
    ):
        raise ValueError(f"WOFF2 identity or metrics differ: {postscript}")
    checked_glyphs = set()
    for codepoint, glyph in ttf.getBestCmap().items():
        web_glyph = web.getBestCmap()[codepoint]
        if web["hmtx"].metrics[web_glyph][0] != ttf["hmtx"].metrics[glyph][0]:
            raise ValueError(f"WOFF2 advance differs: {postscript} U+{codepoint:04X}")
        glyph_pair = (glyph, web_glyph)
        if glyph_pair not in checked_glyphs:
            if ttf["glyf"][glyph].compile(ttf["glyf"]) != web["glyf"][web_glyph].compile(
                web["glyf"]
            ):
                raise ValueError(f"WOFF2 outline differs: {postscript} U+{codepoint:04X}")
            checked_glyphs.add(glyph_pair)


def check_build(recipe_path, output, release=False):
    """Verify existing output only; this command never downloads or rebuilds inputs."""
    recipe_path, output = Path(recipe_path).resolve(), Path(output).resolve()
    recipe = load_recipe(recipe_path)
    version_file = recipe_path.parent / "builder-version"
    if (
        not version_file.is_file()
        or version_file.read_text().strip() != recipe["builder"]["revision"]
    ):
        raise ValueError("Recipe builder revision and builder-version must agree")
    provenance_path = output / "provenance.json"
    if not provenance_path.is_file():
        raise ValueError("Build provenance is missing")
    provenance = json.loads(provenance_path.read_text())
    if (
        provenance.get("preset_id") != recipe["id"]
        or provenance.get("version") != recipe["version"]
    ):
        raise ValueError("Build provenance does not identify this recipe")
    if not valid_source_provenance(recipe, provenance.get("sources"), output / "Sources"):
        raise ValueError("Build provenance does not match the pinned source inputs")
    cmaps = cached_source_cmaps(recipe, output / "Sources")
    recorded_builder = provenance.get("builder")
    if not isinstance(recorded_builder, dict) or set(recorded_builder) != {
        "requested_revision",
        "revision",
        "dirty",
        "development",
    }:
        raise ValueError("Build provenance lacks builder identity")
    variants = {variant.get("family"): variant for variant in provenance.get("variants", [])}
    expected = {family["name"] for family in recipe["families"]}
    if set(variants) != expected:
        raise ValueError("Build provenance variants do not match the recipe")
    checked = []
    for family in recipe["families"]:
        name, ps_name = family["name"], postscript_name(family)
        variant = variants[name]
        metrics = family_metrics(recipe, family)
        if variant.get("metrics") != metrics:
            raise ValueError(f"Provenance metrics differ: {name}")
        for suffix in ("-Regular.ttf", "-Regular.woff2", "_coverage.json"):
            filename = ps_name + suffix
            path = output / filename
            if not path.is_file() or variant.get("files", {}).get(filename) != digest(path):
                raise ValueError(f"Missing or changed build output: {filename}")
        expected_coverage = owners(recipe, family["order"], cmaps)
        coverage_path = output / f"{ps_name}_coverage.json"
        expected_coverage_json = {
            f"U+{codepoint:04X}": source for codepoint, source in sorted(expected_coverage.items())
        }
        if json.loads(coverage_path.read_text()) != expected_coverage_json:
            raise ValueError(f"Coverage map differs from pinned sources: {ps_name}")
        if variant.get("sources") != dict(Counter(expected_coverage.values())):
            raise ValueError(f"Provenance source counts differ: {ps_name}")
        glyphs = validate_font(
            output / f"{ps_name}-Regular.ttf",
            {**recipe, "metrics": metrics},
            name,
            expected_coverage,
            ps_name,
        )
        if variant.get("glyphs") != glyphs:
            raise ValueError(f"Provenance glyph count differs: {ps_name}")
        verify_web_font(
            output / f"{ps_name}-Regular.ttf", output / f"{ps_name}-Regular.woff2", name, ps_name
        )
        checked.append(name)
    for document in recipe["documents"]:
        expected_document = recipe_path.parent / document
        delivered = output / document
        if not delivered.is_file() or digest(delivered) != digest(expected_document):
            raise ValueError(f"Missing or changed release document: {expected_document.name}")
        if delivered.suffix.lower() == ".html":
            verify_document_references(delivered, output)
    if recipe["license_directory"]:
        licenses = recipe_path.parent / recipe["license_directory"]
        for source in licenses.iterdir():
            if source.is_file():
                delivered = output / "Licenses" / source.name
                if not delivered.is_file() or digest(delivered) != digest(source):
                    raise ValueError(f"Missing or changed license: {source.name}")
    state = builder_state()
    if release:
        pinned = recipe["builder"]["revision"]
        if len(pinned) != 40 or any(char not in "0123456789abcdef" for char in pinned):
            raise ValueError("Release recipe must pin a full builder commit")
        if (
            state["dirty"]
            or state["revision"] != pinned
            or recorded_builder["dirty"]
            or recorded_builder["revision"] != pinned
            or recorded_builder["requested_revision"] != pinned
        ):
            raise ValueError("Release build does not use its clean pinned builder revision")
    report = {
        "recipe_id": recipe["id"],
        "recipe_sha256": identity(recipe),
        "checked_families": checked,
        "builder": recorded_builder,
        "release_ready": not state["dirty"] and state["revision"] == recipe["builder"]["revision"],
    }
    return report
