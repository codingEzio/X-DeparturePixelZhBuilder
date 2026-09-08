# DeparturePixelZhBuilder

[English](README.md) · [简体中文](README.zh-Hans.md) · [日本語](README.ja.md) · [한국어](README.ko.md) · [Español](README.es.md)

Build the DeparturePixelZh and DeparturePixelZh Compact fonts from a versioned recipe. The result combines English and Chinese pixel glyphs in one monospaced font, with developer icons.

## Start

Install [uv](https://docs.astral.sh/uv/) and place the DeparturePixelZh font repository beside this repository. Use the builder revision pinned by the font recipe for a release build.

```sh
uv run departurepixelzh-builder build --recipe ../DeparturePixelZh/recipe.json --output Build
uv run departurepixelzh-builder check --recipe ../DeparturePixelZh/recipe.json --output Build --release
uv run -m unittest discover -s tests
```

For local builder development, add `--development` to `build`. This bypasses the revision pin and does not create a release candidate.

## What it does

The recipe fixes the source files and their checksums. The builder selects glyphs, fits them to a shared grid, and writes TTF, WOFF2, coverage maps, checksums, and source records. Latin characters and icons use one cell; full-width characters use two. Compact uses narrower cells. Emoji remain a system fallback.

[docs/how-it-works.md](docs/how-it-works.md) explains glyph selection and spacing.

## Other commands

- `install` installs verified fonts and records a receipt and backups. An interrupted installation is rolled back before the next install proceeds.
- `check-installed` checks installed files against the receipt.
- `sync` copies verified fonts, notices, and source records using a consumer manifest. It stops if managed files have changed outside the tool.
- `package` prepares an archive from checked output.

Use `uv run departurepixelzh-builder --help` or a command's `--help` for arguments. Installation and synchronization stop on unexpected changes so that recovery can be handled explicitly.

## Scope and license

This builder serves the DeparturePixelZh recipe; it is not a general font editor. It does not add bold, italic, or color emoji.

Original builder code uses [MIT](LICENSE-MIT). Generated fonts have separate OFL and component-license requirements. Keep the font repository's notices with redistributed fonts.
