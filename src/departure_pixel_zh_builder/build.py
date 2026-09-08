"""Build static font combinations from a preset; usable by a CLI or a future GUI."""

import argparse
import json
import re
import subprocess
import sys
import time
import unicodedata
from collections import Counter
from contextlib import contextmanager
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.request import urlopen
from zipfile import ZipFile

import brotli
import fontTools
import uharfbuzz as hb
from fontTools import subset
from fontTools.ttLib import TTFont, TTLibError, woff2

from .font_merge import merge_fonts, prepare, private_use

ROOT = Path(__file__).resolve().parent
REPOSITORY_ROOT = ROOT.parents[1]
MAX_SOURCE_BYTES = 20_000_000
METRIC_KEYS = {
    "units_per_em",
    "cell",
    "ascent",
    "descent",
    "line_gap",
    "x_height",
    "cap_height",
    "underline_position",
    "underline_thickness",
    "horizontal_padding",
    "cjk_scale",
    "icon_height",
    "icon_center",
    "mark_gap",
    "outline_error",
}
FAMILY_METRIC_KEYS = {"cell", "horizontal_padding"}


def postscript_name(family):
    return family.get("postscript_name", family["name"])


def digest(path):
    with Path(path).open("rb") as stream:
        result = sha256()
        while block := stream.read(1 << 20):
            result.update(block)
    return result.hexdigest()


def identity(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def builder_state():
    """Return only revision and dirty state; never publish checkout paths."""
    try:
        revision = subprocess.check_output(
            ["git", "-C", str(REPOSITORY_ROOT), "rev-parse", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        dirty = bool(
            subprocess.check_output(
                [
                    "git",
                    "-C",
                    str(REPOSITORY_ROOT),
                    "status",
                    "--porcelain",
                    "--untracked-files=normal",
                ],
                text=True,
                stderr=subprocess.DEVNULL,
            ).strip()
        )
    except (OSError, subprocess.CalledProcessError):
        revision, dirty = None, True
    return {"revision": revision, "dirty": dirty}


def validate_builder_pin(recipe, recipe_path, development=False):
    """Require a committed pin unless an operator explicitly selects development mode."""
    requested = recipe["builder"]["revision"]
    version_file = Path(recipe_path).resolve().parent / "builder-version"
    if not version_file.is_file() or version_file.read_text().strip() != requested:
        raise ValueError("Recipe builder revision and builder-version must agree")
    state = builder_state()
    if development:
        return state
    if not re.fullmatch(r"[a-f0-9]{40}", requested):
        raise ValueError(
            "A normal build requires a full pinned builder commit; use --development explicitly"
        )
    if state["dirty"] or state["revision"] != requested:
        raise ValueError(
            "Builder checkout must be clean and match the recipe pin; use --development explicitly"
        )
    return state


def read_json(path):
    try:
        return json.loads(Path(path).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def write_bytes(path, data):
    path = Path(path)
    if path.is_file() and path.read_bytes() == data:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_name(path.name + ".pending")
    staged.write_bytes(data)
    staged.replace(path)


def write_json(path, value):
    write_bytes(path, (json.dumps(value, indent=2) + "\n").encode())


@contextmanager
def lock_directory(directory, name):
    """One writer per output/installation on the supported macOS/Linux CLI."""
    import fcntl

    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / name).open("a") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError(f"Another operation owns {directory}") from error
        yield


def validate_metrics(metrics):
    if set(metrics) != METRIC_KEYS:
        raise ValueError("Metrics must explicitly define the documented fields")
    if not (
        16 <= metrics["units_per_em"] <= 16384
        and 0 < metrics["cell"] <= 16384
        and 0 < metrics["ascent"] < 32768
        and -32768 < metrics["descent"] <= 0
        and 0 <= metrics["horizontal_padding"] < metrics["cell"]
        and 0 < metrics["cjk_scale"] <= 1
    ):
        raise ValueError("Invalid em size, cell width, line bounds, or padding")


def family_metrics(recipe, family):
    overrides = family.get("metrics", {})
    if not isinstance(overrides, dict) or not set(overrides) <= FAMILY_METRIC_KEYS:
        raise ValueError("Family metrics may override only cell and horizontal_padding")
    result = {**recipe["metrics"], **overrides}
    validate_metrics(result)
    return result


def load_recipe(path):
    path = Path(path).resolve()
    recipe = json.loads(path.read_text())
    required = {
        "schema_version",
        "id",
        "version",
        "font_revision",
        "version_label",
        "vendor",
        "timestamp",
        "copyright",
        "license_description",
        "license_url",
        "emoji_policy",
        "private_use_source",
        "supplement_latin_marks",
        "metrics",
        "sources",
        "families",
        "validation",
        "documents",
        "license_directory",
        "builder",
    }
    if set(recipe) != required or recipe["schema_version"] != 1:
        raise ValueError("Preset must use schema_version 1 and the documented fields")
    builder = recipe["builder"]
    if (
        not isinstance(builder, dict)
        or set(builder) != {"repository", "revision"}
        or builder["repository"] != "DeparturePixelZhBuilder"
        or not isinstance(builder["revision"], str)
        or not builder["revision"]
    ):
        raise ValueError("Recipe must identify its DeparturePixelZhBuilder revision")
    if recipe["emoji_policy"] != "system":
        raise ValueError("This engine supports system emoji fallback, not embedded color fonts")
    validate_metrics(recipe["metrics"])
    if not 1 <= len(recipe["sources"]) <= 8 or not 1 <= len(recipe["families"]) <= 8:
        raise ValueError("Use between one and eight sources/families")
    ids, reserved = set(), []
    for source in recipe["sources"]:
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,40}", source["id"]) or source["id"] in ids:
            raise ValueError("Source IDs must be unique lowercase names")
        ids.add(source["id"])
        if not re.fullmatch(r"[a-f0-9]{64}", source["sha256"]):
            raise ValueError(f"Missing SHA-256 for {source['id']}")
        if source["mode"] not in ("preserve", "mono_fit", "icons"):
            raise ValueError(f"Unknown fitting mode for {source['id']}")
        if source["mode"] == "mono_fit" and not source.get("fullwidth_threshold"):
            raise ValueError("mono_fit requires a positive fullwidth_threshold")
        reserved.extend(source["reserved_names"])
        location = source["location"]
        if not location.startswith(("https://", "http://")):
            local = (path.parent / location).resolve()
            if (
                not local.is_file()
                or local.stat().st_size > MAX_SOURCE_BYTES
                or digest(local) != source["sha256"]
            ):
                raise ValueError(f"Local source is missing or changed: {source['id']}")
    names, postscript_names = set(), set()
    for family in recipe["families"]:
        if set(family) not in (
            {"name", "order"},
            {"name", "order", "metrics"},
            {"name", "order", "postscript_name"},
            {"name", "order", "metrics", "postscript_name"},
        ):
            raise ValueError("Family must define name, order, and optional metrics/postscript_name")
        name, order = family["name"], family["order"]
        ps_name = postscript_name(family)
        if (
            not re.fullmatch(r"[A-Za-z][A-Za-z0-9 -]{0,42}", name)
            or name != name.strip()
            or name in names
            or not re.fullmatch(r"[A-Za-z][A-Za-z0-9-]{0,42}", ps_name)
            or ps_name in postscript_names
        ):
            raise ValueError("Family names and PostScript names must be unique ASCII identifiers")
        if any(term.casefold() in name.casefold() for term in reserved):
            raise ValueError(f"Family name uses a source's reserved name: {name}")
        names.add(name)
        postscript_names.add(ps_name)
        if not order or len(order) != len(set(order)) or not set(order) <= ids:
            raise ValueError(f"Unknown or duplicate source in {name}")
        if recipe["private_use_source"] is not None and recipe["private_use_source"] not in order:
            raise ValueError("The private-use source must be included in every family")
        family_metrics(recipe, family)
    for document in recipe["documents"]:
        relative = Path(document)
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or relative == Path(".")
            or not (path.parent / relative).is_file()
        ):
            raise ValueError(f"Missing distribution document: {document}")
    if (
        recipe["license_directory"] is not None
        and not (path.parent / recipe["license_directory"]).is_dir()
    ):
        raise ValueError("Missing license directory")
    return recipe


def load_inputs(recipe, preset, sources_dir):
    payloads, cmaps, provenance = {}, {}, {}
    for source in recipe["sources"]:
        key, location = source["id"], source["location"]
        path = sources_dir / (key + (".zip" if source["member"] else ".ttf"))
        if not path.is_file() or digest(path) != source["sha256"]:
            if location.startswith(("https://", "http://")):
                with urlopen(location, timeout=40) as response:
                    data = response.read(MAX_SOURCE_BYTES + 1)
            else:
                data = (preset.parent / location).read_bytes()
            if len(data) > MAX_SOURCE_BYTES or sha256(data).hexdigest() != source["sha256"]:
                raise ValueError(f"Source size/checksum failed: {key}")
            write_bytes(path, data)
        data = path.read_bytes()
        if source["member"]:
            with ZipFile(BytesIO(data)) as archive:
                if archive.getinfo(source["member"]).file_size > MAX_SOURCE_BYTES:
                    raise ValueError(f"Font member exceeds size limit: {key}")
                data = archive.read(source["member"])
        font = TTFont(BytesIO(data), recalcTimestamp=False)
        if any(tag in font for tag in ("fvar", "CFF2", "COLR", "sbix", "SVG ", "CBDT")) or not (
            "glyf" in font or "CFF " in font
        ):
            raise ValueError(
                f"Use a static outline font; variable/color fonts are unsupported: {key}"
            )
        payloads[key], cmaps[key] = data, font.getBestCmap()
        provenance[key] = {
            "url": location,
            "archive_sha256": source["sha256"],
            "member": source["member"],
            "font_sha256": sha256(data).hexdigest(),
            "embedded_version": font["name"].getDebugName(5),
        }
    return payloads, cmaps, provenance


def valid_source_provenance(recipe, provenance, sources_dir=None):
    """Only reuse cached output when provenance still exactly describes pinned inputs."""
    if not isinstance(provenance, dict) or set(provenance) != {
        source["id"] for source in recipe["sources"]
    }:
        return False
    for source in recipe["sources"]:
        record = provenance[source["id"]]
        if (
            not isinstance(record, dict)
            or set(record) != {"url", "archive_sha256", "member", "font_sha256", "embedded_version"}
            or record["url"] != source["location"]
            or record["archive_sha256"] != source["sha256"]
            or record["member"] != source["member"]
            # Version strings are useful provenance when an upstream supplies them, but
            # valid static test fonts and some older upstreams do not have name ID 5.
            # The pinned archive and extracted-font digests remain the authority.
            or not (
                isinstance(record["embedded_version"], str) or record["embedded_version"] is None
            )
            or not re.fullmatch(r"[a-f0-9]{64}", record["font_sha256"])
        ):
            return False
        if sources_dir is not None:
            archive = Path(sources_dir) / (source["id"] + (".zip" if source["member"] else ".ttf"))
            if not archive.is_file() or digest(archive) != source["sha256"]:
                return False
            archive_data = archive.read_bytes()
            if source["member"]:
                try:
                    with ZipFile(BytesIO(archive_data)) as contents:
                        font_data = contents.read(source["member"])
                except (KeyError, ValueError):
                    return False
            else:
                font_data = archive_data
            try:
                font = TTFont(BytesIO(font_data), recalcTimestamp=False)
            except (OSError, TTLibError, ValueError):
                return False
            if (
                sha256(font_data).hexdigest() != record["font_sha256"]
                or font["name"].getDebugName(5) != record["embedded_version"]
            ):
                return False
    return True


def owners(recipe, order, cmaps):
    result = {}
    icon_source = recipe["private_use_source"]
    if icon_source:
        result.update({cp: icon_source for cp in cmaps[icon_source] if private_use(cp)})
    for key in order:
        for cp in cmaps[key]:
            if cp not in (0x200D, 0xFE0E, 0xFE0F) and not 0x1F000 <= cp <= 0x1FAFF:
                result.setdefault(cp, key)
    return result


def validate_font(path, recipe, family, expected=None, postscript=None):
    font = TTFont(path, recalcTimestamp=False)
    cmap, metrics = font.getBestCmap(), recipe["metrics"]
    postscript = postscript or family
    if (
        font["name"].getDebugName(1) != family
        or font["name"].getDebugName(6) != postscript + "-Regular"
    ):
        raise ValueError(f"Incorrect font identity: {family}")
    if expected is not None and set(cmap) != set(expected):
        raise ValueError(f"Merged coverage differs from source priority: {family}")
    cell = metrics["cell"]
    codepoints_by_glyph = {}
    for codepoint, name in cmap.items():
        codepoints_by_glyph.setdefault(name, []).append(codepoint)
    for name in set(cmap.values()):
        glyph = font["glyf"][name]
        advance = font["hmtx"].metrics[name][0]
        if advance not in (0, cell, cell * 2):
            raise ValueError(
                f"Non-cell advance in {family}: {name}; use mono_fit or adjust the preset"
            )
        if hasattr(glyph, "yMax") and (
            glyph.yMax > metrics["ascent"] or glyph.yMin < metrics["descent"]
        ):
            raise ValueError(f"Glyph exceeds line bounds in {family}: {name}")
        if (
            advance
            and not all(
                unicodedata.category(chr(cp)).startswith("M") for cp in codepoints_by_glyph[name]
            )
            and hasattr(glyph, "xMax")
            and (glyph.xMin < 0 or glyph.xMax > advance)
        ):
            raise ValueError(f"Glyph exceeds cell bounds in {family}: {name}")
    shaper = hb.Font(hb.Face(Path(path).read_bytes()))
    samples = recipe["validation"]
    for kind, text in [("single", samples["single_cell"]), ("double", samples["double_cell"])] + [
        ("cluster", value) for value in samples["combining"] + samples["icons"]
    ]:
        if not text:
            continue
        buf = hb.Buffer()
        buf.add_str(text)
        buf.guess_segment_properties()
        hb.shape(shaper, buf)
        cursor = 0
        for info, pos in zip(buf.glyph_infos, buf.glyph_positions):
            glyph = font["glyf"][font.getGlyphName(info.codepoint)]
            if not info.codepoint:
                raise ValueError(f"Missing sample glyph in {family}: {text!r}")
            if kind in ("single", "double") and pos.x_advance != cell * (
                2 if kind == "double" else 1
            ):
                raise ValueError(f"Shaped cell mismatch in {family}: {text!r}")
            if hasattr(glyph, "yMax") and (
                pos.y_offset + glyph.yMax > metrics["ascent"]
                or pos.y_offset + glyph.yMin < metrics["descent"]
            ):
                raise ValueError(f"Positioned accent exceeds line bounds in {family}: {text!r}")
            if (
                kind == "cluster"
                and hasattr(glyph, "xMax")
                and not (
                    0
                    <= cursor + pos.x_offset + glyph.xMin
                    <= cursor + pos.x_offset + glyph.xMax
                    <= cell
                )
            ):
                raise ValueError(f"Mark/icon escapes its cell in {family}: {text!r}")
            cursor += pos.x_advance
        if kind == "cluster" and cursor != cell:
            raise ValueError(f"Cluster advance mismatch in {family}: {text!r}")
    return len(font.getGlyphOrder())


def valid_file(directory, record, name):
    path = directory / name
    return (
        name in record.get("files", {}) and path.is_file() and digest(path) == record["files"][name]
    )


def run_build(
    preset,
    output,
    formats=("ttf", "woff2"),
    progress=None,
    force=False,
    development=False,
):
    """Return JSON-ready results; progress events are suitable for a CLI or GUI worker."""
    started = time.perf_counter()
    preset, output = Path(preset).resolve(), Path(output).resolve()
    recipe = load_recipe(preset)
    builder_snapshot = validate_builder_pin(recipe, preset, development)
    if not formats or not set(formats) <= {"ttf", "woff2"}:
        raise ValueError("Formats must be ttf and/or woff2")
    emit = progress or (lambda event: None)
    engine_key = identity(
        {
            "code": {name: digest(ROOT / name) for name in ("build.py", "font_merge.py")},
            "fonttools": fontTools.__version__,
            "brotli": brotli.__version__,
            "harfbuzz": hb.__version__,
            "unicode": unicodedata.unidata_version,
        }
    )
    shared_recipe = {field: value for field, value in recipe.items() if field != "families"}
    key = identity({"engine": engine_key, "recipe": recipe})
    with lock_directory(output, ".build.lock"):
        sources_dir = output / "Sources"
        sources_dir.mkdir(exist_ok=True)
        build_state = read_json(output / "build_state.json")
        old = build_state.get("families", {})
        prepared_index = {}
        if not force:
            for sidecar in sources_dir.glob("*.prepared.json"):
                entry = read_json(sidecar)
                path = sidecar.with_name(sidecar.name.removesuffix(".prepared.json") + ".ttf")
                if path.is_file() and entry.get("sha256") == digest(path):
                    prepared_index[entry["key"]] = path
        payloads = cmaps = None
        provenance = build_state.get("sources", {})
        if not valid_source_provenance(recipe, provenance, sources_dir):
            emit({"stage": "sources"})
            payloads, cmaps, provenance = load_inputs(recipe, preset, sources_dir)
        records, rebuilt, reused = {}, [], []
        with TemporaryDirectory(prefix=".build-", dir=output) as temporary:
            stage = Path(temporary)
            for family in recipe["families"]:
                name, ps_name = family["name"], postscript_name(family)
                metrics = family_metrics(recipe, family)
                effective_recipe = {**recipe, "metrics": metrics}
                family_key = identity(
                    {"engine": engine_key, "recipe": shared_recipe, "family": family}
                )
                ttf, web, coverage_name = (
                    ps_name + "-Regular.ttf",
                    ps_name + "-Regular.woff2",
                    ps_name + "_coverage.json",
                )
                previous = old.get(name, {})
                cached = (
                    not force
                    and previous.get("key") == family_key
                    and valid_file(output, previous, ttf)
                    and valid_file(output, previous, coverage_name)
                )
                if cached:
                    record = dict(previous)
                    record["files"] = dict(previous["files"])
                    reused.append(name)
                    emit({"stage": "reuse", "family": name})
                else:
                    if payloads is None:
                        emit({"stage": "sources"})
                        payloads, cmaps, provenance = load_inputs(recipe, preset, sources_dir)
                    ownership = owners(recipe, family["order"], cmaps)
                    paths = []
                    by_id = {source["id"]: source for source in recipe["sources"]}
                    for source_id in family["order"]:
                        source = by_id[source_id]
                        selected = sorted(
                            cp for cp, owner in ownership.items() if owner == source_id
                        )
                        if not selected:
                            continue
                        prepared_key = identity(
                            {
                                "engine": engine_key,
                                "source": source,
                                "metrics": metrics,
                                "source_cell": recipe["metrics"]["cell"],
                                "selected": selected,
                            }
                        )
                        path = sources_dir / f"{ps_name}_{source_id}.ttf"
                        if prepared_key in prepared_index:
                            if prepared_index[prepared_key] != path:
                                write_bytes(path, prepared_index[prepared_key].read_bytes())
                            emit({"stage": "reuse_prepared", "family": name, "source": source_id})
                        else:
                            emit({"stage": "prepare", "family": name, "source": source_id})
                            font = TTFont(BytesIO(payloads[source_id]), recalcTimestamp=False)
                            options = subset.Options()
                            options.layout_features, options.notdef_outline, options.glyph_names = (
                                ["*"],
                                True,
                                True,
                            )
                            sub = subset.Subsetter(options=options)
                            sub.populate(unicodes=selected)
                            sub.subset(font)
                            prepare(font, source, metrics, recipe["metrics"]["cell"])
                            font.save(path)
                            prepared_index[prepared_key] = path
                        write_json(
                            path.with_suffix(".prepared.json"),
                            {"key": prepared_key, "sha256": digest(path)},
                        )
                        paths.append(path)
                    emit({"stage": "merge", "family": name})
                    font = merge_fonts(paths, effective_recipe, name, ps_name)
                    font.save(stage / ttf)
                    glyphs = validate_font(stage / ttf, effective_recipe, name, ownership, ps_name)
                    write_json(
                        stage / coverage_name,
                        {f"U+{cp:04X}": owner for cp, owner in sorted(ownership.items())},
                    )
                    record = {
                        "key": family_key,
                        "family": name,
                        "postscript_name": ps_name,
                        "metrics": metrics,
                        "glyphs": glyphs,
                        "sources": dict(Counter(ownership.values())),
                        "files": {
                            ttf: digest(stage / ttf),
                            coverage_name: digest(stage / coverage_name),
                        },
                    }
                    rebuilt.append(name)
                if "woff2" in formats and not (cached and valid_file(output, previous, web)):
                    emit({"stage": "woff2", "family": name})
                    woff2.compress(output / ttf if cached else stage / ttf, stage / web)
                    record["files"][web] = digest(stage / web)
                records[name] = record
            # All requested fonts have validated before replacing any delivered font.
            for path in stage.iterdir():
                path.replace(output / path.name)
        for document in recipe["documents"]:
            source = preset.parent / document
            write_bytes(output / document, source.read_bytes())
        if recipe["license_directory"]:
            for source in (preset.parent / recipe["license_directory"]).iterdir():
                if source.is_file():
                    write_bytes(output / "Licenses" / source.name, source.read_bytes())
        report = {
            "version": recipe["version"],
            "preset_id": recipe["id"],
            "key": key,
            "sources": provenance,
            "variants": [
                {field: value for field, value in record.items() if field != "key"}
                for record in records.values()
            ],
            "metrics": recipe["metrics"],
            "builder": {
                "requested_revision": recipe["builder"]["revision"],
                "development": development,
                **builder_snapshot,
            },
            "rebuilt": rebuilt,
            "reused": reused,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
        }
        write_json(output / "build_state.json", {"families": records, "sources": provenance})
        write_json(output / "provenance.json", report)
        return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--recipe", "--preset", dest="preset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--formats", nargs="+", choices=("ttf", "woff2"), default=("ttf", "woff2"))
    parser.add_argument("--force", action="store_true", help="Recompute fonts and prepared sources")
    parser.add_argument(
        "--development", action="store_true", help="Record an explicitly unpinned development build"
    )
    args = parser.parse_args(argv)
    import resource

    resource.setrlimit(resource.RLIMIT_CPU, (180, 180))
    result = run_build(
        args.preset,
        args.output,
        args.formats,
        lambda event: print(json.dumps(event), file=sys.stderr),
        args.force,
        args.development,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
