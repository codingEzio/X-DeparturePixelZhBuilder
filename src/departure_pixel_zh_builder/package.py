"""Assemble a deterministic, verified DeparturePixelZh release archive."""

import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from .build import digest, load_recipe
from .check import check_build


def release_files(recipe, output):
    """The full allowlist for a release archive; caches never enter a delivery."""
    files = []
    for family in recipe["families"]:
        postscript = family.get("postscript_name", family["name"])
        files.extend(
            [
                output / f"{postscript}-Regular.ttf",
                output / f"{postscript}-Regular.woff2",
                output / f"{postscript}_coverage.json",
            ]
        )
    files.extend(output / document for document in recipe["documents"])
    files.append(output / "provenance.json")
    license_directory = recipe.get("license_directory")
    if license_directory:
        source_licenses = Path(recipe["_recipe_path"]).parent / license_directory
        files.extend(
            output / "Licenses" / source.name
            for source in sorted(source_licenses.iterdir())
            if source.is_file()
        )
    return files


def package_release(recipe_path, output, destination):
    """Write a release zip only from a clean, pinned builder and verified output."""
    recipe_path, output, destination = (
        Path(recipe_path).resolve(),
        Path(output).resolve(),
        Path(destination).resolve(),
    )
    check_build(recipe_path, output, release=True)
    recipe = {**load_recipe(recipe_path), "_recipe_path": str(recipe_path)}
    root = f"DeparturePixelZh-{recipe['version']}"
    files = release_files(recipe, output)
    if len(set(files)) != len(files) or not all(path.is_file() for path in files):
        raise ValueError("Verified release output is incomplete")
    checksums = "".join(
        f"{digest(path)}  {path.relative_to(output).as_posix()}\n" for path in sorted(files)
    ).encode()
    destination.mkdir(parents=True, exist_ok=True)
    archive = destination / f"{root}.zip"
    temporary = archive.with_suffix(".pending")
    if temporary.exists():
        raise ValueError(f"Unexpected pending archive: {temporary}")
    with ZipFile(temporary, "w", compression=ZIP_DEFLATED, compresslevel=9) as zip_file:
        for path in sorted(files):
            entry = ZipInfo(f"{root}/{path.relative_to(output).as_posix()}")
            entry.date_time = (2026, 9, 8, 0, 0, 0)
            entry.compress_type = ZIP_DEFLATED
            zip_file.writestr(entry, path.read_bytes(), compress_type=ZIP_DEFLATED, compresslevel=9)
        entry = ZipInfo(f"{root}/SHA256SUMS")
        entry.date_time = (2026, 9, 8, 0, 0, 0)
        entry.compress_type = ZIP_DEFLATED
        zip_file.writestr(entry, checksums, compress_type=ZIP_DEFLATED, compresslevel=9)
    temporary.replace(archive)
    with ZipFile(archive) as zip_file:
        names = set(zip_file.namelist())
        if f"{root}/SHA256SUMS" not in names or len(names) != len(files) + 1:
            raise ValueError("Release archive readback failed")
    return {
        "archive": str(archive),
        "sha256": digest(archive),
        "files": len(files) + 1,
        "manifest": json.loads((output / "provenance.json").read_text())["version"],
    }
