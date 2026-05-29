# 256 Bits: Volume 1

This directory contains the committed source controls for the first Obscure Bit book. It should hold manifests, editorial notes, release rules, art direction, and book source assets. It should not hold paid book exports, private scans, or rights reports.

Planning documents:

- `PRODUCTION_PLAN.md` defines the book workflow and release gates.
- `FONT_PLAN.md` defines the open font system and audition candidates.
- `COLOR_PLAN.md` defines the light and dark edition palettes.

Manual draft build:

```bash
uv run --python .venv/bin/python scripts/book_build.py --allow-incomplete
```

Release build, once 256 entries are selected and all blockers are cleared:

```bash
uv run --python .venv/bin/python scripts/book_build.py
```

Generated files are written under `book/output/volume-1/`, which is ignored by Git.

Tracked book source assets live here:

- `book/source/volume-1/assets/art/`
- `book/source/volume-1/assets/modes/`
- `book/source/volume-1/assets/sections/`

Private local-only material belongs under `private/`, which is ignored by Git:

- `private/rights-reports/volume-1/`

The intended release object is a curated 16 by 16 selection indexed `00` through `FF`, not a chronological dump of the site.
