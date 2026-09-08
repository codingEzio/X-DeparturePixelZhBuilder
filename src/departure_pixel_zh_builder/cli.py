"""Command line interface for deterministic DeparturePixelZh builds."""

import argparse
import json
import sys

from . import build, install
from .audit import require_clean
from .check import check_build
from .package import package_release
from .sync import sync


def _print(value):
    print(json.dumps(value, indent=2, sort_keys=True))


def main(argv=None):
    parser = argparse.ArgumentParser(prog="departurepixelzh-builder")
    commands = parser.add_subparsers(dest="command", required=True)
    build_parser = commands.add_parser("build")
    build_parser.add_argument("--recipe", required=True)
    build_parser.add_argument("--output", required=True)
    build_parser.add_argument(
        "--formats", nargs="+", choices=("ttf", "woff2"), default=("ttf", "woff2")
    )
    build_parser.add_argument("--force", action="store_true")
    build_parser.add_argument("--development", action="store_true")
    check_parser = commands.add_parser("check")
    check_parser.add_argument("--recipe", required=True)
    check_parser.add_argument("--output", required=True)
    check_parser.add_argument("--release", action="store_true")
    install_parser = commands.add_parser("install")
    install_parser.add_argument("--recipe", required=True)
    install_parser.add_argument("--output", required=True)
    install_parser.add_argument("--destination", required=True)
    install_parser.add_argument("--receipt", required=True)
    install_parser.add_argument("--development", action="store_true")
    installed_parser = commands.add_parser("check-installed")
    installed_parser.add_argument("--recipe", required=True)
    installed_parser.add_argument("--destination", required=True)
    installed_parser.add_argument("--receipt", required=True)
    sync_parser = commands.add_parser("sync")
    sync_parser.add_argument("--recipe", required=True)
    sync_parser.add_argument("--manifest", required=True)
    sync_parser.add_argument("--output", required=True)
    sync_parser.add_argument("--dry-run", action="store_true")
    sync_parser.add_argument("--check", action="store_true")
    audit_parser = commands.add_parser("audit-public")
    audit_parser.add_argument("--root", required=True)
    package_parser = commands.add_parser("package")
    package_parser.add_argument("--recipe", required=True)
    package_parser.add_argument("--output", required=True)
    package_parser.add_argument("--destination", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "build":
            result = build.run_build(
                args.recipe,
                args.output,
                args.formats,
                force=args.force,
                development=args.development,
            )
        elif args.command == "check":
            result = check_build(args.recipe, args.output, args.release)
        elif args.command == "install":
            result = install.install_fonts(
                args.recipe,
                args.output,
                args.destination,
                args.receipt,
                development=args.development,
            )
        elif args.command == "check-installed":
            result = install.check_install(args.recipe, args.destination, args.receipt)
        elif args.command == "sync":
            result = sync(args.recipe, args.manifest, args.output, args.dry_run, args.check)
        elif args.command == "audit-public":
            result = require_clean(args.root)
        else:
            result = package_release(args.recipe, args.output, args.destination)
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as error:
        print(f"DeparturePixelZhBuilder failed: {error}", file=sys.stderr)
        return 1
    _print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
