# Production Plan

## Positioning

`256 Bits: Volume 1` is a curated 16 by 16 art book drawn from Obscure Bit. The release should feel like a field guide, a strange technical manual, and a collector's archive. It is not a chronological export of the website.

## Required Release Components

- 256 selected entries indexed `00` through `FF`
- 16 named sections with 16 entries each
- approved creator note
- approved machines note
- approved editor/agents letter
- one final art plate or intentionally designed visual treatment per selected bit
- QR target for every selected bit
- text originality/IP validation report
- art provenance manifest
- source/provenance appendix
- colophon with tools, typography, edition, and build information
- Gumroad bundle README, license, and certificate

## Book Craft Checklist

- Every entry has a byte index, original date, title, section, theme, QR target, and editor caption.
- Every entry has a layout mode: `signal`, `archive`, `field_note`, `protocol`, `myth`, or `glitch`.
- Every art plate has a recorded provider, model/tool, prompt, date, human approval, and rights note.
- Section dividers have their own visual language and explain the curatorial register of the next 16 entries.
- The contents page is a single 16 by 16 memory map, with each cell carrying byte index, section color, and layout mode.
- The PDF has front cover, back cover, spine mockup, endpaper pattern, title page, copyright page, contents, and colophon.
- The sample edition contains enough spreads to prove the object, not just tease it.
- The QR index is tested before release.
- The Gumroad ZIP contains only release-approved files.
- The release build embeds the approved open-font files and includes their license texts.

## Art Lanes

`auto_draft` is for fast visual exploration. The default planned model is `black-forest-labs/flux.1-schnell` through NVIDIA NIM/build.nvidia.com because it was identified as the best free/low-friction commercial-use candidate.

`manual_hero` is for final art created or revised by the human using Gemini image generation / Nano Banana, Midjourney, or another approved tool. Hero art should be used for cover candidates, section openers, and entries where the automatic draft is not strong enough.

## Forbidden Art Inputs

- living-artist style imitation
- named fictional characters or universes
- brand marks and logos
- celebrity likenesses
- copied reference images without rights
- prompts that ask for recognizable protected product designs

## Local Build

Draft build:

```bash
uv run --python .venv/bin/python scripts/book_build.py --allow-incomplete
```

Release build:

```bash
uv run --python .venv/bin/python scripts/book_build.py
```

The release build exits non-zero while there are blockers.

## Generated Outputs

All outputs go under `book-output/volume-1/`, which is ignored by Git:

- `256-bits-volume-1.md`
- `validation-report.md`
- `source-manifest.json`
- `art-briefs.yaml`
- `qr-targets.csv`
- `candidate-scorecard.csv`
- `gumroad/README.txt`

## Private Inputs

Private assets belong under `private/`, which is ignored by Git:

- `private/book-assets/volume-1/art/`
- `private/rights-reports/volume-1/`

## Curation Workflow

1. Run the draft build.
2. Fill out `book-output/volume-1/candidate-scorecard.csv` with curation scores, IP risk, notes, and selected status.
3. Move the final selected slugs into `book/volume-1/manifest.yaml` under `selected_entries`.
4. Generate or manually create art from `book-output/volume-1/art-briefs.yaml`.
5. Record approved final art in `book/volume-1/art_manifest.yaml`.
6. Finalize `book/volume-1/creator-note.md`.
7. Finalize `book/volume-1/machines-note.md`.
8. Finalize `book/volume-1/editor-letter.md`.
9. Re-run the build until `validation-report.md` has zero blockers.
