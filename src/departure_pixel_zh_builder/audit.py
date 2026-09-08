"""Focused public-artifact audit without adding a secret-scanning dependency."""

import re
import struct
import subprocess
import zlib
from pathlib import Path

PRIVATE_PATTERNS = {
    "private absolute path": re.compile(r"/(?:Users|private)/(?!public/)"),
    "private workspace reference": re.compile(
        r"(?:^|[^A-Za-z0-9])(?:"
        + "X/"
        + r"(?:Project|Setup)|"
        + "agent"
        + "-run|model"
        + "-routing)(?:$|[^A-Za-z0-9])",
        re.IGNORECASE,
    ),
    "authenticated URL": re.compile(r"https?://[^/\s:@]+:[^/\s@]+@", re.IGNORECASE),
    "access token": re.compile(r"\b(?:ghp|github_pat|sk-[A-Za-z0-9_]{2,})[A-Za-z0-9_-]{12,}\b"),
    "private key": re.compile(r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----"),
}
DISALLOWED_ARTIFACT_SUFFIXES = {".ttf", ".otf", ".woff", ".woff2", ".zip", ".pyc"}
SAFE_PNG = Path("specimens/native-specimen.png")
MAX_SPECIMEN_PNG_BYTES = 2_000_000


def png_violation(path, relative):
    """Allow only the inspected public native specimen, with dimension-only EXIF."""
    if relative != SAFE_PNG:
        return f"unexpected binary image: {relative}"
    data = path.read_bytes()
    if len(data) > MAX_SPECIMEN_PNG_BYTES or not data.startswith(b"\x89PNG\r\n\x1a\n"):
        return f"invalid specimen PNG: {relative}"
    position, chunks, dimensions, exif = 8, [], None, None
    while position < len(data):
        if position + 12 > len(data):
            return f"invalid specimen PNG: {relative}"
        size = struct.unpack(">I", data[position : position + 4])[0]
        end = position + 12 + size
        if end > len(data):
            return f"invalid specimen PNG: {relative}"
        kind, payload, checksum = (
            data[position + 4 : position + 8],
            data[position + 8 : position + 8 + size],
            data[position + 8 + size : end],
        )
        if struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF) != checksum:
            return f"invalid specimen PNG: {relative}"
        chunks.append(kind)
        if kind == b"IHDR" and len(payload) == 13:
            dimensions = struct.unpack(">II", payload[:8])
        elif kind == b"eXIf":
            exif = payload
        position = end
    if (
        position != len(data)
        or dimensions is None
        or not all(0 < value <= 10_000 for value in dimensions)
        or chunks[:3] != [b"IHDR", b"sRGB", b"eXIf"]
        or chunks[-1:] != [b"IEND"]
        or any(kind not in {b"IHDR", b"sRGB", b"eXIf", b"IDAT", b"IEND"} for kind in chunks)
        or b"IDAT" not in chunks
        or not _dimension_only_exif(exif, dimensions)
    ):
        return f"unsafe specimen PNG metadata: {relative}"
    return None


def _dimension_only_exif(data, dimensions):
    """Accept the exact TIFF shape written by the native specimen capture: width/height only."""
    if not isinstance(data, bytes) or len(data) != 56 or data[:8] != b"MM\x00*\x00\x00\x00\x08":
        return False
    count = struct.unpack(">H", data[8:10])[0]
    if (
        count != 1
        or data[10:22] != b"\x87i\x00\x04\x00\x00\x00\x01\x00\x00\x00\x1a"
        or data[22:26] != b"\x00\x00\x00\x00"
    ):
        return False
    if struct.unpack(">H", data[26:28])[0] != 2 or data[52:56] != b"\x00\x00\x00\x00":
        return False
    entries = [data[28:40], data[40:52]]
    expected = [(0xA002, dimensions[0]), (0xA003, dimensions[1])]
    return all(
        struct.unpack(">H", entry[:2])[0] == tag
        and entry[2:8] == b"\x00\x04\x00\x00\x00\x01"
        and struct.unpack(">I", entry[8:12])[0] == value
        for entry, (tag, value) in zip(entries, expected)
    )


def candidate_files(root):
    root = Path(root).resolve()
    try:
        output = subprocess.check_output(
            ["git", "-C", str(root), "ls-files", "--cached", "--others", "--exclude-standard"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
        return [root / line for line in output.splitlines() if line]
    except (OSError, subprocess.CalledProcessError):
        return [path for path in root.rglob("*") if path.is_file() and ".git" not in path.parts]


def audit(root):
    """Return violations from every publish candidate, including upstream notices."""
    root = Path(root).resolve()
    violations = []
    for path in candidate_files(root):
        relative = path.relative_to(root)
        if path.is_symlink():
            violations.append(f"symbolic link publish candidate: {relative}")
            continue
        if path.suffix.lower() == ".png":
            if violation := png_violation(path, relative):
                violations.append(violation)
            continue
        if path.suffix.lower() in DISALLOWED_ARTIFACT_SUFFIXES:
            violations.append(f"generated or binary artifact: {relative}")
            continue
        try:
            content = path.read_text()
        except UnicodeDecodeError:
            violations.append(f"non-text publish candidate: {relative}")
            continue
        for label, pattern in PRIVATE_PATTERNS.items():
            if pattern.search(content):
                violations.append(f"{label}: {relative}")
    return violations


def require_clean(root):
    violations = audit(root)
    if violations:
        raise ValueError("Publication audit failed: " + "; ".join(violations))
    return {"root": str(Path(root).resolve()), "checked_files": len(candidate_files(root))}
