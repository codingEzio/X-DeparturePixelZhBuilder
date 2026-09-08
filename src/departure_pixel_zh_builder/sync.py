"""Atomically synchronize a verified font build into a declared consumer."""

import json
import os
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory

from .build import digest, identity, lock_directory, write_json
from .check import check_build


def _contained(root, relative, label):
    relative = Path(relative)
    if relative.is_absolute() or ".." in relative.parts or relative == Path("."):
        raise ValueError(f"{label} must remain inside the consumer root")
    root = Path(root).resolve()
    candidate = root / relative
    if candidate.is_symlink() or not candidate.parent.resolve().is_relative_to(root):
        raise ValueError(f"Refusing symbolic link or escaped {label}: {relative}")
    return candidate


def _manifest(path):
    path = Path(path).resolve()
    value = json.loads(path.read_text())
    if (
        not isinstance(value, dict)
        or set(value) != {"schema_version", "id", "receipt", "font", "files"}
        or value["schema_version"] != 1
        or not isinstance(value["id"], str)
        or not isinstance(value["receipt"], str)
        or not isinstance(value["files"], list)
        or not value["files"]
        or not isinstance(value["font"], dict)
        or set(value["font"]) != {"version", "recipe_sha256", "families"}
        or not isinstance(value["font"]["version"], str)
        or not isinstance(value["font"]["recipe_sha256"], str)
        or not isinstance(value["font"]["families"], list)
    ):
        raise ValueError("Invalid consumer manifest")
    families = value["font"]["families"]
    if (
        not families
        or any(
            not isinstance(family, dict)
            or set(family) != {"postscript_name", "sha256"}
            or not isinstance(family["postscript_name"], str)
            or not re_full_sha(family["sha256"])
            for family in families
        )
        or len({family["postscript_name"] for family in families}) != len(families)
    ):
        raise ValueError("Consumer font pin must name each selected family and checksum")
    files = []
    for entry in value["files"]:
        if not isinstance(entry, dict) or set(entry) != {"source", "destination"}:
            raise ValueError("Each consumer file needs source and destination")
        source, destination = entry["source"], entry["destination"]
        if not isinstance(source, str) or not isinstance(destination, str):
            raise TypeError("Consumer file paths must be strings")
        if Path(source).name != source:
            raise ValueError("Consumer sources must be build-root filenames")
        _contained(path.parent, destination, "consumer destination")
        files.append((source, destination))
    if len({destination for _, destination in files}) != len(files):
        raise ValueError("Consumer destinations must be unique")
    _contained(path.parent, value["receipt"], "consumer receipt")
    return path, value, files


def re_full_sha(value):
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(char in "0123456789abcdef" for char in value)
    )


def _read_receipt(path, manifest_id):
    if not path.exists():
        return None
    if not path.is_file() or path.is_symlink():
        raise ValueError("Consumer receipt must be a regular file")
    value = json.loads(path.read_text())
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != 1
        or value.get("manifest_id") != manifest_id
        or not isinstance(value.get("font"), dict)
        or not isinstance(value.get("files"), dict)
    ):
        raise ValueError("Consumer receipt does not match the manifest")
    return value


def _sync_journal(root):
    return _contained(root, ".departurepixelzh-sync-journal.json", "sync journal")


def _sync_recovery_root(root):
    return _contained(root, ".departurepixelzh-sync-recovery", "sync recovery directory")


def _restore_sync_backup(root, recovery, key, checksum):
    backup = recovery / key
    if not backup.is_file() or digest(backup) != checksum:
        raise ValueError(f"Synchronization recovery backup is unavailable: {key}")
    target = _contained(root, key, "synchronization recovery target")
    target.parent.mkdir(parents=True, exist_ok=True)
    staged = target.with_name(target.name + ".recovery")
    if staged.exists():
        raise ValueError(f"Unexpected synchronization recovery file: {staged}")
    shutil.copy2(backup, staged)
    os.replace(staged, target)


def recover_pending_sync(root, receipt, manifest_id):
    """Restore an interrupted synchronization before examining a new consumer state."""
    journal, recovery = _sync_journal(root), _sync_recovery_root(root)
    if not journal.exists():
        return False
    if (
        not journal.is_file()
        or journal.is_symlink()
        or not recovery.is_dir()
        or recovery.is_symlink()
    ):
        raise ValueError("Synchronization journal is invalid; recover it manually")
    value = json.loads(journal.read_text())
    required = {
        "schema_version",
        "manifest_id",
        "receipt_before",
        "receipt_after",
        "changed",
        "retired",
    }
    if (
        not isinstance(value, dict)
        or set(value) != required
        or value["schema_version"] != 1
        or value["manifest_id"] != manifest_id
        or not isinstance(value["changed"], dict)
        or not isinstance(value["retired"], dict)
        or (value["receipt_before"] is not None and not isinstance(value["receipt_before"], dict))
        or not isinstance(value["receipt_after"], dict)
    ):
        raise ValueError("Synchronization journal is invalid; recover it manually")
    for key, record in value["changed"].items():
        if (
            not isinstance(record, dict)
            or set(record) != {"before", "after"}
            or not re_full_sha(record["after"])
            or (record["before"] is not None and not re_full_sha(record["before"]))
        ):
            raise ValueError("Synchronization journal file record is invalid; recover it manually")
        target = _contained(root, key, "synchronization recovery target")
        if target.exists() and not target.is_file():
            raise ValueError(f"Synchronization recovery target is not a regular file: {key}")
        actual = digest(target) if target.is_file() else None
        if record["before"] is None:
            if actual is None:
                continue
            if actual != record["after"]:
                raise ValueError(f"Synchronization recovery target changed: {key}")
            target.unlink()
        elif actual != record["before"]:
            if actual not in {None, record["after"]}:
                raise ValueError(f"Synchronization recovery target changed: {key}")
            _restore_sync_backup(root, recovery, key, record["before"])
    for key, checksum in value["retired"].items():
        if not re_full_sha(checksum):
            raise ValueError(
                "Synchronization journal retirement record is invalid; recover it manually"
            )
        target = _contained(root, key, "synchronization recovery target")
        if target.exists() and not target.is_file():
            raise ValueError(f"Synchronization recovery target is not a regular file: {key}")
        actual = digest(target) if target.is_file() else None
        if actual != checksum:
            if actual is not None:
                raise ValueError(f"Synchronization recovery target changed: {key}")
            _restore_sync_backup(root, recovery, key, checksum)
    current_receipt = (
        json.loads(receipt.read_text()) if receipt.exists() and receipt.is_file() else None
    )
    if current_receipt not in (None, value["receipt_before"], value["receipt_after"]):
        raise ValueError("Synchronization receipt changed during recovery")
    if value["receipt_before"] is None:
        if receipt.exists():
            receipt.unlink()
    else:
        write_json(receipt, value["receipt_before"])
    journal.unlink()
    shutil.rmtree(recovery)
    return True


def sync(recipe_path, manifest_path, output, dry_run=False, check=False):
    manifest_path, manifest, entries = _manifest(manifest_path)
    root, output = manifest_path.parent, Path(output).resolve()
    receipt = _contained(root, manifest["receipt"], "consumer receipt")
    with lock_directory(root, ".departurepixelzh-sync.lock"):
        recover_pending_sync(root, receipt, manifest["id"])
    build_report = check_build(recipe_path, output)
    recipe = json.loads(Path(recipe_path).read_text())
    provenance = json.loads((output / "provenance.json").read_text())
    expected_font = manifest["font"]
    if (
        expected_font["version"] != provenance["version"]
        or expected_font["recipe_sha256"] != build_report["recipe_sha256"]
    ):
        raise ValueError("Consumer manifest does not pin this font version and recipe")
    output_families = {
        variant.get("postscript_name", variant["family"]): variant["files"].get(
            variant.get("postscript_name", variant["family"]) + "-Regular.ttf"
        )
        for variant in provenance["variants"]
    }
    manifest_families = {
        family["postscript_name"]: family["sha256"] for family in expected_font["families"]
    }
    if manifest_families != output_families:
        raise ValueError("Consumer manifest font-family checksums do not match the verified build")
    required_manifest_sources = {"NOTICE.md", "OFL.txt", "provenance.json"}
    declared_sources = {source for source, _ in entries}
    if not required_manifest_sources <= declared_sources:
        raise ValueError("Consumer manifest must include NOTICE.md, OFL.txt, and provenance.json")
    wanted = {}
    for source, relative_destination in entries:
        source_path = output / source
        if not source_path.is_file():
            raise ValueError(f"Verified build does not contain {source}")
        destination = _contained(root, relative_destination, "consumer destination")
        if destination == receipt:
            raise ValueError("Consumer receipt cannot overwrite a synchronized asset")
        wanted[destination] = {"source": source, "sha256": digest(source_path)}
    license_root = output / "Licenses"
    if license_root.exists() and not license_root.is_dir():
        raise ValueError("Verified build license directory is not a directory")
    if license_root.is_dir():
        for source_path in sorted(path for path in license_root.rglob("*") if path.is_file()):
            source = str(source_path.relative_to(output))
            relative_destination = Path("Licenses") / source_path.relative_to(license_root)
            destination = _contained(root, relative_destination, "consumer license destination")
            if destination in wanted:
                raise ValueError(
                    f"Consumer manifest conflicts with required component notice: {source}"
                )
            wanted[destination] = {"source": source, "sha256": digest(source_path)}
    previous = _read_receipt(receipt, manifest["id"])
    previous_files = previous["files"] if previous else {}
    changes, unchanged, observed = [], [], {}
    for destination, record in wanted.items():
        key = str(destination.relative_to(root))
        actual = digest(destination) if destination.is_file() else None
        if destination.exists() and not destination.is_file():
            raise ValueError(f"Consumer destination is not a regular file: {key}")
        if actual == record["sha256"]:
            unchanged.append(key)
        elif (
            key in previous_files and actual == previous_files[key].get("sha256") or actual is None
        ):
            changes.append(key)
        else:
            raise ValueError(f"Refusing to replace a modified or unmanaged consumer file: {key}")
        observed[key] = actual
    retired = [
        key for key in previous_files if key not in {str(path.relative_to(root)) for path in wanted}
    ]
    retired_observed = {}
    for key in retired:
        path = _contained(root, key, "consumer retirement target")
        actual = digest(path) if path.is_file() else None
        if path.exists() and not path.is_file():
            raise ValueError(f"Consumer retirement target is not a regular file: {key}")
        if actual is not None and actual != previous_files[key].get("sha256"):
            raise ValueError(f"Refusing to retire modified consumer file: {key}")
        retired_observed[key] = actual
    result = {
        "changed": changes,
        "unchanged": unchanged,
        "retired": retired,
        "receipt": str(receipt),
        "recipe_sha256": build_report["recipe_sha256"],
        "font_version": provenance["version"],
    }
    if dry_run:
        return {**result, "dry_run": True}
    if check:
        if changes or retired or previous is None:
            raise ValueError("Consumer assets do not match the verified build")
        return {**result, "checked": True}
    with (
        lock_directory(root, ".departurepixelzh-sync.lock"),
        TemporaryDirectory(prefix=".departurepixelzh-sync-", dir=root) as temporary,
    ):
        stage = Path(temporary)
        # The preflight ran before acquiring the lock so it does not hold a consumer
        # hostage while checking the build. Re-read every managed path under the lock;
        # otherwise a concurrent editor could replace a previously managed file between
        # preflight and os.replace.
        for destination in wanted:
            key = str(destination.relative_to(root))
            if destination.exists() and not destination.is_file():
                raise ValueError(f"Consumer destination changed during synchronization: {key}")
            actual = digest(destination) if destination.is_file() else None
            if actual != observed[key]:
                raise ValueError(f"Consumer file changed during synchronization: {key}")
        for key in retired:
            path = _contained(root, key, "consumer retirement target")
            if path.exists() and not path.is_file():
                raise ValueError(
                    f"Consumer retirement target changed during synchronization: {key}"
                )
            actual = digest(path) if path.is_file() else None
            if actual != retired_observed[key]:
                raise ValueError(f"Consumer retirement changed during synchronization: {key}")
        old = {}
        for destination in wanted:
            key = str(destination.relative_to(root))
            if destination.is_file():
                old[key] = stage / f"old-{len(old)}"
                shutil.copy2(destination, old[key])
        for key in retired:
            path = _contained(root, key, "consumer retirement target")
            if path.is_file():
                old[key] = stage / f"old-{len(old)}"
                shutil.copy2(path, old[key])
        receipt_copy = stage / "receipt"
        if receipt.exists():
            shutil.copy2(receipt, receipt_copy)
        files = {
            str(destination.relative_to(root)): record for destination, record in wanted.items()
        }
        new_receipt = {
            "schema_version": 1,
            "manifest_id": manifest["id"],
            "font": {"version": provenance["version"], "recipe_sha256": identity(recipe)},
            "files": files,
        }
        journal, recovery = _sync_journal(root), _sync_recovery_root(root)
        if journal.exists() or recovery.exists():
            raise ValueError("Unexpected synchronization recovery state")
        recovery.mkdir()
        for key, backup in old.items():
            durable = recovery / key
            durable.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup, durable)
        transaction = {
            "schema_version": 1,
            "manifest_id": manifest["id"],
            "receipt_before": previous,
            "receipt_after": new_receipt,
            "changed": {
                str(destination.relative_to(root)): {
                    "before": observed[str(destination.relative_to(root))],
                    "after": record["sha256"],
                }
                for destination, record in wanted.items()
                if str(destination.relative_to(root)) in changes
            },
            "retired": {
                key: retired_observed[key] for key in retired if retired_observed[key] is not None
            },
        }
        write_json(journal, transaction)
        if json.loads(journal.read_text()) != transaction:
            raise ValueError("Synchronization journal readback failed")
        try:
            for destination, record in wanted.items():
                key = str(destination.relative_to(root))
                destination.parent.mkdir(parents=True, exist_ok=True)
                staged = stage / f"new-{len(old)}-{destination.name}"
                shutil.copy2(output / record["source"], staged)
                if digest(staged) != record["sha256"]:
                    raise ValueError(f"Staged file checksum failed: {destination.name}")
                actual = digest(destination) if destination.is_file() else None
                if actual != observed[key] or (destination.exists() and not destination.is_file()):
                    raise ValueError(f"Consumer file changed during synchronization: {key}")
                os.replace(staged, destination)
                if digest(destination) != record["sha256"]:
                    raise ValueError(f"Synced file checksum failed: {destination.name}")
            for key in retired:
                path = _contained(root, key, "consumer retirement target")
                if path.exists():
                    actual = digest(path) if path.is_file() else None
                    if actual != retired_observed[key] or not path.is_file():
                        raise ValueError(
                            f"Consumer retirement changed during synchronization: {key}"
                        )
                    path.unlink()
            write_json(receipt, new_receipt)
            if json.loads(receipt.read_text()) != new_receipt:
                raise ValueError("Synchronization receipt readback failed")
            journal.unlink()
            shutil.rmtree(recovery)
        except BaseException:
            for destination in wanted:
                key = str(destination.relative_to(root))
                if key in old:
                    os.replace(old[key], destination)
                elif destination.exists():
                    destination.unlink()
            for key in retired:
                path = _contained(root, key, "consumer retirement target")
                if key in old:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(old[key], path)
            if receipt_copy.exists():
                os.replace(receipt_copy, receipt)
            elif receipt.exists():
                receipt.unlink()
            if journal.exists():
                journal.unlink()
            if recovery.exists():
                shutil.rmtree(recovery)
            raise
    return result
