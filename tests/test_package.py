"""Release archives contain exactly the verified delivery allowlist."""

import hashlib
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from zipfile import ZipFile

from departure_pixel_zh_builder import package
from departure_pixel_zh_builder.check import verify_document_references


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.recipe_path = self.root / "recipe.json"
        self.output = self.root / "Build"
        self.output.mkdir()
        self.destination = self.root / "dist"
        self.recipe = {
            "version": "1.2.3",
            "families": [{"name": "Sample", "postscript_name": "SamplePS"}],
            "documents": [
                "README.md",
                "NOTICE.md",
                "OFL.txt",
                "specimens/index.html",
                "specimens/outline.svg",
            ],
            "license_directory": "licenses",
        }
        (self.root / "licenses").mkdir()
        (self.root / "licenses" / "Required.txt").write_text("required notice\n")
        for name, content in {
            "SamplePS-Regular.ttf": b"ttf",
            "SamplePS-Regular.woff2": b"woff2",
            "SamplePS_coverage.json": b"{}\n",
            "README.md": b"readme\n",
            "NOTICE.md": b"notice\n",
            "OFL.txt": b"ofl\n",
            "provenance.json": b'{"version": "1.2.3"}\n',
        }.items():
            (self.output / name).write_bytes(content)
        (self.output / "specimens").mkdir()
        (self.output / "specimens" / "index.html").write_text("<a href='../README.md'>readme</a>\n")
        (self.output / "specimens" / "outline.svg").write_text("<svg/>\n")
        (self.output / "Licenses").mkdir()
        (self.output / "Licenses" / "Required.txt").write_text("required notice\n")

    def package(self):
        with (
            patch.object(package, "check_build"),
            patch.object(package, "load_recipe", return_value=self.recipe),
        ):
            return package.package_release(self.recipe_path, self.output, self.destination)

    def test_archive_is_deterministic_and_excludes_stale_outputs(self):
        (self.output / "Old-Regular.ttf").write_bytes(b"stale font")
        (self.output / "Licenses" / "Old.txt").write_text("stale notice\n")
        first = self.package()
        archive = Path(first["archive"])
        first_bytes = archive.read_bytes()
        second = self.package()
        self.assertEqual(first["sha256"], second["sha256"])
        self.assertEqual(first_bytes, archive.read_bytes())
        root = "DeparturePixelZh-1.2.3/"
        with ZipFile(archive) as zip_file:
            names = set(zip_file.namelist())
            self.assertEqual(
                names,
                {
                    root + name
                    for name in (
                        "SamplePS-Regular.ttf",
                        "SamplePS-Regular.woff2",
                        "SamplePS_coverage.json",
                        "README.md",
                        "NOTICE.md",
                        "OFL.txt",
                        "specimens/index.html",
                        "specimens/outline.svg",
                        "provenance.json",
                        "Licenses/Required.txt",
                        "SHA256SUMS",
                    )
                },
            )
            sums = zip_file.read(root + "SHA256SUMS").decode().splitlines()
            for line in sums:
                checksum, relative = line.split("  ", 1)
                self.assertEqual(
                    checksum, hashlib.sha256(zip_file.read(root + relative)).hexdigest()
                )

    def test_failed_release_check_does_not_write_an_archive(self):
        with (
            patch.object(package, "check_build", side_effect=ValueError("not release-ready")),
            patch.object(package, "load_recipe", return_value=self.recipe),
            self.assertRaisesRegex(ValueError, "not release-ready"),
        ):
            package.package_release(self.recipe_path, self.output, self.destination)
        self.assertFalse(self.destination.exists())

    def test_specimen_relative_urls_resolve_in_built_and_packaged_layouts(self):
        specimen = self.output / "specimens" / "index.html"
        (self.output / "SamplePS-Regular.woff2").write_bytes(b"woff2")
        (self.output / "specimens" / "outline.svg").write_text("<svg/>\n")
        specimen.write_text(
            "<style>@font-face{src:url('../SamplePS-Regular.woff2')}</style>"
            "<img src='outline.svg'><a href='../README.md'>readme</a>\n"
        )
        verify_document_references(specimen, self.output)
        result = self.package()
        with TemporaryDirectory() as temporary, ZipFile(result["archive"]) as zip_file:
            zip_file.extractall(temporary)
            root = Path(temporary) / "DeparturePixelZh-1.2.3"
            verify_document_references(root / "specimens" / "index.html", root)


if __name__ == "__main__":
    unittest.main()
