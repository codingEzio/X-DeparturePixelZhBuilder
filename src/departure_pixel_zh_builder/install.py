"""Install validated fonts with receipt ownership and transactional replacement."""

import argparse
import json
import os
import re
import shutil
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from .build import (
    digest,
    identity,
    load_recipe,
    lock_directory,
    postscript_name,
    run_build,
    write_json,
)


def plain_path(path):
    path = Path(os.path.abspath(path))
    if path.is_symlink():
        raise ValueError(f"Refusing symbolic link: {path}")
    return path.parent.resolve() / path.name


def overlapping_directories(first, second):
    """Whether two resolved directory roots would allow one operation to touch the other."""
    return first.is_relative_to(second) or second.is_relative_to(first)


def expected_names(recipe):
    names = [(family["name"], postscript_name(family)) for family in recipe["families"]]
    if (
        not names
        or len({postscript for _, postscript in names}) != len(names)
        or any(
            not re.fullmatch(r"[A-Za-z][A-Za-z0-9-]{0,42}", postscript) for _, postscript in names
        )
    ):
        raise ValueError("Invalid font families in preset")
    return {
        postscript + "-Regular.ttf": {"family": family, "postscript_name": postscript}
        for family, postscript in names
    }


def read_receipt(path, destination):
    if not path.exists():
        return None
    if not path.is_file():
        raise ValueError(f"Receipt is not a regular file: {path}")
    value = json.loads(path.read_text())
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != 1
        or value.get("destination") != str(destination)
    ):
        raise ValueError("Receipt schema or destination does not match")
    files = value.get("files")
    if (
        not isinstance(files, dict)
        or not files
        or any(
            not re.fullmatch(r"[A-Za-z][A-Za-z0-9-]{0,42}-Regular\.ttf", name)
            or not isinstance(record, dict)
            or not isinstance(record.get("family"), str)
            or record.get("postscript_name", record["family"]) + "-Regular.ttf" != name
            or not re.fullmatch(r"[a-f0-9]{64}", record.get("sha256", ""))
            for name, record in files.items()
        )
    ):
        raise ValueError("Invalid font ownership receipt")
    return value


def _restore_backup(destination, receipt, name, checksum):
    backup = plain_path(receipt.parent / "Backups" / checksum / name)
    if not backup.is_file() or digest(backup) != checksum:
        raise ValueError(f"Installation recovery backup is unavailable: {name}")
    target = plain_path(destination / name)
    staged = target.with_name(target.name + ".recovery")
    if staged.exists():
        raise ValueError(f"Unexpected installation recovery file: {staged}")
    shutil.copy2(backup, staged)
    os.replace(staged, target)


def recover_pending(destination, receipt):
    """Roll a durable incomplete installation back to its recorded receipt state."""
    destination, receipt = plain_path(destination), plain_path(receipt)
    pending = plain_path(receipt.with_name(receipt.name + ".install-journal.json"))
    if not pending.exists():
        return False
    if not pending.is_file():
        raise ValueError(f"Installation journal is not a regular file: {pending}")
    value = json.loads(pending.read_text())
    required = {
        "schema_version",
        "destination",
        "receipt_before",
        "receipt_after",
        "changed",
        "retired",
    }
    if (
        not isinstance(value, dict)
        or set(value) != required
        or value["schema_version"] != 1
        or value["destination"] != str(destination)
        or not isinstance(value["changed"], dict)
        or not isinstance(value["retired"], dict)
        or (value["receipt_before"] is not None and not isinstance(value["receipt_before"], dict))
        or not isinstance(value["receipt_after"], dict)
    ):
        raise ValueError("Installation journal is invalid; recover it manually")
    for name, record in value["changed"].items():
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9-]{0,42}-Regular\.ttf", name) or (
            not isinstance(record, dict)
            or set(record) != {"before", "after"}
            or not re.fullmatch(r"[a-f0-9]{64}", record["after"])
            or (
                record["before"] is not None and not re.fullmatch(r"[a-f0-9]{64}", record["before"])
            )
        ):
            raise ValueError("Installation journal font record is invalid; recover it manually")
        target = plain_path(destination / name)
        actual = digest(target) if target.is_file() else None
        if target.exists() and not target.is_file():
            raise ValueError(f"Installation recovery target is not a regular file: {name}")
        if record["before"] is None:
            if actual is None:
                continue
            if actual != record["after"]:
                raise ValueError(f"Installation recovery target changed: {name}")
            target.unlink()
        elif actual != record["before"]:
            if actual not in {None, record["after"]}:
                raise ValueError(f"Installation recovery target changed: {name}")
            _restore_backup(destination, receipt, name, record["before"])
    for name, checksum in value["retired"].items():
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9-]{0,42}-Regular\.ttf", name) or not re.fullmatch(
            r"[a-f0-9]{64}", checksum
        ):
            raise ValueError(
                "Installation journal retirement record is invalid; recover it manually"
            )
        target = plain_path(destination / name)
        actual = digest(target) if target.is_file() else None
        if target.exists() and not target.is_file():
            raise ValueError(f"Installation recovery target is not a regular file: {name}")
        if actual != checksum:
            if actual is not None:
                raise ValueError(f"Installation recovery target changed: {name}")
            _restore_backup(destination, receipt, name, checksum)
    current_receipt = (
        json.loads(receipt.read_text()) if receipt.exists() and receipt.is_file() else None
    )
    if current_receipt not in (None, value["receipt_before"], value["receipt_after"]):
        raise ValueError("Installation receipt changed during recovery")
    if value["receipt_before"] is None:
        if receipt.exists():
            receipt.unlink()
    else:
        write_json(receipt, value["receipt_before"])
    pending.unlink()
    return True


def check_install(preset, destination, receipt):
    """Check only the preset, receipt and installed files; no sources or build required."""
    preset = Path(preset).resolve()
    destination, receipt = plain_path(destination), plain_path(receipt)
    recipe = json.loads(preset.read_text())
    if recipe.get("schema_version") != 1:
        raise ValueError("Unsupported preset schema")
    expected = expected_names(recipe)
    record = read_receipt(receipt, destination)
    if record is None:
        raise ValueError(f"Installation receipt is missing: {receipt}")
    if record.get("preset_id") != recipe["id"] or record.get("preset_fingerprint") != identity(
        recipe
    ):
        raise ValueError("Installed fonts do not match the current preset")
    if set(record["files"]) != set(expected):
        raise ValueError("Installed font names do not match the preset")
    for name, identity_record in expected.items():
        path = plain_path(destination / name)
        if record["files"][name]["family"] != identity_record["family"] or not path.is_file():
            raise ValueError(f"Installed font is missing or has wrong identity: {name}")
        if digest(path) != record["files"][name]["sha256"]:
            raise ValueError(f"Installed font was modified: {name}")
    return {"checked": list(expected), "receipt": str(receipt)}


def install_fonts(preset, output, destination, receipt, development=False):
    preset, output = Path(preset).resolve(), Path(output).resolve()
    destination, receipt = plain_path(destination), plain_path(receipt)
    if overlapping_directories(output, destination) or overlapping_directories(
        output, receipt.parent
    ):
        raise ValueError("Build output must be disjoint from installation and receipt state")
    recipe = load_recipe(preset)
    names = expected_names(recipe)
    if receipt == destination or receipt.name in names and receipt.parent == destination:
        raise ValueError("Receipt must be separate from font files")
    # Build and validate all outputs before inspecting or changing installation files.
    # Keep a shared release output self-consistent: installation consumes TTFs, but
    # rebuilding both declared formats prevents provenance from dropping WOFF2 hashes.
    build_options = {"formats": ("ttf", "woff2")}
    if development:
        build_options["development"] = True
    report = run_build(preset, output, **build_options)
    wanted = {
        name: {**identity_record, "sha256": digest(output / name)}
        for name, identity_record in names.items()
    }
    for variant in report["variants"]:
        name = variant.get("postscript_name", variant["family"]) + "-Regular.ttf"
        if name in wanted and wanted[name]["sha256"] != variant["files"][name]:
            raise ValueError(f"Build output changed: {name}")
    plain_path(receipt.parent / ".departurepixelzh-receipt.lock")
    plain_path(destination / ".departurepixelzh-install.lock")
    with (
        lock_directory(receipt.parent, ".departurepixelzh-receipt.lock"),
        lock_directory(destination, ".departurepixelzh-install.lock"),
    ):
        plain_path(destination)
        plain_path(receipt)
        recover_pending(destination, receipt)
        old = read_receipt(receipt, destination)
        if old and old.get("preset_id") != recipe["id"]:
            raise ValueError("Receipt belongs to another preset")
        previous = old["files"] if old else {}
        retired = {name: record for name, record in previous.items() if name not in names}
        current, changed, unchanged = {}, [], []
        # Every destination must pass before the first destination font is written.
        for name in names:
            target = plain_path(destination / name)
            if target.exists() and not target.is_file():
                raise ValueError(f"Font target is not a regular file: {name}")
            actual = digest(target) if target.exists() else None
            current[name] = actual
            if actual is not None and name in previous and actual != previous[name]["sha256"]:
                raise ValueError(f"Managed font was modified: {name}")
            if actual is not None and name not in previous and actual != wanted[name]["sha256"]:
                raise ValueError(f"Refusing to replace an unmanaged font: {name}")
            (unchanged if actual == wanted[name]["sha256"] else changed).append(name)
        retired_current = {}
        for name, record in retired.items():
            target = plain_path(destination / name)
            if target.exists() and not target.is_file():
                raise ValueError(f"Font target is not a regular file: {name}")
            actual = digest(target) if target.exists() else None
            if actual is not None and actual != record["sha256"]:
                raise ValueError(f"Managed font was modified: {name}")
            retired_current[name] = actual
        removed = [name for name, actual in retired_current.items() if actual is not None]
        new = {
            "schema_version": 1,
            "preset_id": recipe["id"],
            "preset_fingerprint": identity(recipe),
            "destination": str(destination),
            "files": wanted,
        }
        if not changed and not removed and old == new:
            return {"changed": [], "unchanged": unchanged, "removed": [], "receipt": str(receipt)}
        pending = plain_path(receipt.with_name(receipt.name + ".install-journal.json"))
        if pending.exists():
            raise ValueError(f"Unexpected pending receipt: {pending}")
        with TemporaryDirectory(prefix=".font-install-", dir=destination) as temporary:
            stage = Path(temporary)
            receipt_copy = stage / "previous_receipt"
            if receipt.exists():
                shutil.copy2(receipt, receipt_copy)
            for name in changed:
                shutil.copy2(output / name, stage / name)
                if digest(stage / name) != wanted[name]["sha256"]:
                    raise ValueError(f"Staged font checksum failed: {name}")
            backed_up = [(name, current[name]) for name in changed if current[name] is not None]
            backed_up.extend((name, retired_current[name]) for name in removed)
            for name, sha256 in backed_up:
                backup_root = plain_path(receipt.parent / "Backups")
                backup_directory = plain_path(backup_root / sha256)
                backup = plain_path(backup_directory / name)
                backup.parent.mkdir(parents=True, exist_ok=True)
                if backup.exists():
                    if not backup.is_file() or digest(backup) != sha256:
                        raise ValueError(f"Conflicting font backup: {backup}")
                else:
                    shutil.copy2(destination / name, backup)
                if digest(backup) != sha256:
                    raise ValueError(f"Backup checksum failed: {name}")
                shutil.copy2(backup, stage / (name + ".old"))
            transaction = {
                "schema_version": 1,
                "destination": str(destination),
                "receipt_before": old,
                "receipt_after": new,
                "changed": {
                    name: {"before": current[name], "after": wanted[name]["sha256"]}
                    for name in changed
                },
                "retired": {name: retired_current[name] for name in removed},
            }
            write_json(pending, transaction)
            if json.loads(pending.read_text()) != transaction:
                raise ValueError("Installation journal readback failed")
            replaced, retired_files = [], []
            try:
                for name in changed:
                    target = plain_path(destination / name)
                    actual = digest(target) if target.is_file() else None
                    if actual != current[name] or (target.exists() and not target.is_file()):
                        raise ValueError(f"Font changed during installation: {name}")
                    os.replace(stage / name, target)
                    replaced.append(name)
                for name in removed:
                    target = plain_path(destination / name)
                    actual = digest(target) if target.is_file() else None
                    if actual != retired_current[name] or (
                        target.exists() and not target.is_file()
                    ):
                        raise ValueError(f"Font changed during installation: {name}")
                    os.unlink(target)
                    retired_files.append(name)
                for name in names:
                    target = plain_path(destination / name)
                    if digest(target) != wanted[name]["sha256"]:
                        raise ValueError(f"Installed font checksum failed: {name}")
                write_json(receipt, new)
                if json.loads(receipt.read_text()) != new:
                    raise ValueError("Receipt readback failed")
                pending.unlink()
            except BaseException:
                for name in reversed(retired_files):
                    os.replace(stage / (name + ".old"), destination / name)
                for name in reversed(replaced):
                    target = destination / name
                    if current[name] is None:
                        target.unlink()
                    else:
                        os.replace(stage / (name + ".old"), target)
                if receipt_copy.exists():
                    os.replace(receipt_copy, receipt)
                elif receipt.exists():
                    receipt.unlink()
                if pending.exists():
                    pending.unlink()
                raise
    return {"changed": changed, "unchanged": unchanged, "removed": removed, "receipt": str(receipt)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--recipe", "--preset", dest="preset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--development", action="store_true")
    args = parser.parse_args(argv)
    import resource

    resource.setrlimit(resource.RLIMIT_CPU, (180, 180))
    try:
        if args.check:
            result = check_install(args.preset, args.destination, args.receipt)
        else:
            result = install_fonts(
                args.preset, args.output, args.destination, args.receipt, args.development
            )
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as error:
        print(f"Font installation failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
