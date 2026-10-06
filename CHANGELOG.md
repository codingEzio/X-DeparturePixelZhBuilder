# Changelog

## 0.1.1

- Add `package --format desktop` for TTF and `package --format web` for WOFF2. The default `all` keeps the combined package.
- Retain all source notices and checksums in each package; leave browser-only HTML and CSS out of desktop packages.
- Verify relative CSS asset URLs during release checks.
- Add direct desktop and web font downloads and a tagged builder source release.

## 0.1.0

- Reproducible font merging, source and glyph verification, installation, consumer synchronization, publication audit, and deterministic combined archives.
