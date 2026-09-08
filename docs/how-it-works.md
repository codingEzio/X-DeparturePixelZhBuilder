# How the composition works

## A glyph is not its width

Every Unicode character maps to a glyph outline and an advance width. The outline is the drawing; the advance is where the next character starts. DeparturePixelZh keeps a common baseline and line box, then gives Latin one cell and full-width characters two cells.

```text
Latin “A”                    Chinese “中”
0        700                0                         1400
|-- outline --| advance     |------ outline ------|     advance
             ^ next glyph                         ^ next glyph
```

The Compact family uses 650 and 1,300-unit advances. It does not merely squeeze CSS: the builder refits outlines and positioning data into the new cells.

## Source selection

For `DeparturePixelZh`, Departure Mono wins characters available in both text sources; Cubic 11 supplies remaining Chinese coverage. Nerd Fonts Symbols Mono owns private-use icons. The recipe records the order so a rebuild cannot accidentally change the provider.

## Fitting and verification

The builder normalizes all sources to 1,100 units per em, adjusts Cubic's optical scale, aligns icon and box-drawing geometry, and retains supported combining-mark attachments. It then verifies glyph ownership, bounds, advance widths, name tables, the system-emoji exclusion, and WOFF2 coverage parity.

The resulting font is a static Regular face. A system emoji font supplies emoji sequences at rendering time.
