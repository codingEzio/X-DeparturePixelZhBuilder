# DeparturePixelZhBuilder

This repository builds the DeparturePixelZh font family from an explicit recipe.

- Run `uv run departurepixelzh-builder build --recipe ../DeparturePixelZh/recipe.json --output Build`.
- Run `uv run departurepixelzh-builder check --recipe ../DeparturePixelZh/recipe.json --output Build` before packaging.
- Use `uv run -m unittest discover -s tests` for builder tests.
- Keep source URLs, checksums, font identity, and licensing material in the font repository.
- Do not add generic font-editor features unless a DeparturePixelZh recipe needs them.
- Generated fonts, downloaded archives, installation receipts, and backups do not belong in Git.
