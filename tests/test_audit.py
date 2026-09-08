import struct
import unittest
import zlib
from pathlib import Path
from tempfile import TemporaryDirectory

from departure_pixel_zh_builder.audit import audit


class AuditTests(unittest.TestCase):
    def specimen_png(self, width=32, height=16, extra_chunk=None):
        def chunk(kind, payload):
            return (
                struct.pack(">I", len(payload))
                + kind
                + payload
                + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
            )

        exif = (
            b"MM\x00*\x00\x00\x00\x08\x00\x01\x87i\x00\x04\x00\x00\x00\x01\x00\x00\x00\x1a\x00\x00\x00\x00"
            + b"\x00\x02\xa0\x02\x00\x04\x00\x00\x00\x01"
            + struct.pack(">I", width)
            + b"\xa0\x03\x00\x04\x00\x00\x00\x01"
            + struct.pack(">I", height)
            + b"\x00\x00\x00\x00"
        )
        payloads = [
            chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)),
            chunk(b"sRGB", b"\x00"),
            chunk(b"eXIf", exif),
        ]
        if extra_chunk:
            payloads.append(chunk(*extra_chunk))
        payloads.extend([chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00")), chunk(b"IEND", b"")])
        return b"\x89PNG\r\n\x1a\n" + b"".join(payloads)

    def test_public_upstream_contact_is_allowed_but_private_metadata_is_not(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "licenses").mkdir()
            (root / "licenses" / "UPSTREAM.txt").write_text(
                "author@example.com https://example.com\n"
            )
            (root / "README.md").write_text("public\n")
            self.assertEqual(audit(root), [])
            (root / "README.md").write_text("/" + "Users/example/private\n")
            self.assertEqual(audit(root), ["private absolute path: README.md"])

    def test_upstream_license_cannot_exempt_private_operational_references(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            license_file = root / "licenses" / "Bad.txt"
            license_file.parent.mkdir()
            license_file.write_text(
                "mirror https://user"
                + ":pass@example.com/archive\n"
                + "run agent"
                + "-run with model"
                + "-routing\n"
            )
            self.assertEqual(
                audit(root),
                [
                    "private workspace reference: licenses/Bad.txt",
                    "authenticated URL: licenses/Bad.txt",
                ],
            )

    def test_binary_delivery_artifacts_are_not_public_source(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "font.ttf").write_bytes(b"font")
            self.assertEqual(audit(root), ["generated or binary artifact: font.ttf"])

    def test_tests_are_scanned_and_symbolic_links_are_rejected(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "tests").mkdir()
            (root / "tests" / "fixture.py").write_text("workspace = 'X/" + "Project'\n")
            self.assertEqual(audit(root), ["private workspace reference: tests/fixture.py"])
            (root / "tests" / "fixture.py").write_text("public = True\n")
            (root / "README.md").write_text("public\n")
            (root / "linked").symlink_to(root / "README.md")
            self.assertEqual(audit(root), ["symbolic link publish candidate: linked"])

    def test_only_dimension_only_native_specimen_png_is_allowed(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            specimen = root / "specimens" / "native-specimen.png"
            specimen.parent.mkdir()
            specimen.write_bytes(self.specimen_png())
            self.assertEqual(audit(root), [])
            specimen.write_bytes(self.specimen_png(extra_chunk=(b"tEXt", b"Author=private")))
            self.assertEqual(
                audit(root), ["unsafe specimen PNG metadata: specimens/native-specimen.png"]
            )

    def test_social_card_is_allowed_only_as_a_metadata_free_public_png(self):
        def chunk(kind, payload):
            return (
                struct.pack(">I", len(payload))
                + kind
                + payload
                + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
            )

        social_png = b"\x89PNG\r\n\x1a\n" + b"".join(
            [
                chunk(b"IHDR", struct.pack(">IIBBBBB", 1600, 900, 8, 3, 0, 0, 0)),
                chunk(b"PLTE", b"\x00\x00\x00"),
                chunk(b"IDAT", zlib.compress(b"\x00\x00")),
                chunk(b"IEND", b""),
            ]
        )
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            card = root / "assets" / "departurepixelzh-social-card.png"
            card.parent.mkdir()
            card.write_bytes(social_png)
            self.assertEqual(audit(root), [])
            card.write_bytes(social_png.replace(b"IEND", b"tEXt"))
            self.assertEqual(
                audit(root), ["unsafe social card PNG metadata: assets/departurepixelzh-social-card.png"]
            )
