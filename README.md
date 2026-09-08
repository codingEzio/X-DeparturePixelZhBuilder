# DeparturePixelZhBuilder

DeparturePixelZhBuilder composes the **DeparturePixelZh** and **DeparturePixelZh Compact** font families from a versioned recipe. It preserves a source's selected glyph outlines while normalizing metrics, widths, metadata, and private-use icon coverage.

The builder is licensed under [MIT](LICENSE-MIT). The generated fonts have their own OFL and component-notice requirements in the font repository.

## Commands

```sh
uv run departurepixelzh-builder build --development --recipe ../DeparturePixelZh/recipe.json --output Build
uv run departurepixelzh-builder check --recipe ../DeparturePixelZh/recipe.json --output Build --release
uv run departurepixelzh-builder install --development --recipe ../DeparturePixelZh/recipe.json --output Build \
  --destination "$HOME/Library/Fonts" --receipt Local/install.json
uv run departurepixelzh-builder check-installed --recipe ../DeparturePixelZh/recipe.json \
  --destination "$HOME/Library/Fonts" --receipt Local/install.json
uv run departurepixelzh-builder package --recipe ../DeparturePixelZh/recipe.json \
  --output ../DeparturePixelZh/Build --destination ../DeparturePixelZh/dist
```

`sync` consumes a declared consumer manifest. It copies a verified output, notices, and sanitized provenance together. It never changes a destination whose managed files differ from its recorded hashes.

Install creates a durable sibling `receipt.json.install-journal.json` before changing a managed font. A later install rolls that incomplete transaction back from its checksum-addressed `Backups/` copy before it does new work. If a journal or target was changed outside the installer, it stops and reports the exact target for manual recovery.

## How it works

1. The recipe pins every upstream archive or file with a SHA-256 digest.
2. Its family order decides which source supplies an overlapping Unicode character. Nerd Fonts owns its private-use mappings.
3. Each selected source is converted to 1,100 units per em. Latin and icons advance one cell; full-width glyphs advance two.
4. Cubic 11 outlines are fitted into that grid at the recipe's optical scale. Compact changes advance width from 700/1,400 to 650/1,300 and refits outlines and mark anchors.
5. The builder writes family metadata, coverage maps, TTF, WOFF2, checksums, and provenance. Emoji stay with the platform fallback.

See [docs/how-it-works.md](docs/how-it-works.md) for the distinction between an outline's drawing bounds and its advance width.
