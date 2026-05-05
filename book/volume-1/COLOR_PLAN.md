# Color Plan

The book ships as paired light and dark editions. Both palettes should feel like the same artifact under different reading conditions: the light edition is paper, archive, rust, and oxidized teal; the dark edition is night, screen glow, ember, and brass.

These colors are production targets from `design.yaml`. Release files should use these exact values unless a print proof shows a contrast or reproduction problem.

## Light Edition: Paper Archive

| Role | Name | Hex | Use |
| --- | --- | --- | --- |
| Paper | Warm Archive Paper | `#f3efe4` | Main page background. |
| Secondary Paper | Aged Folder | `#ebe4d4` | Section pages, subtle bands, plate backgrounds. |
| Ink | Near-Black Brown | `#171512` | Primary text, QR marks, high-contrast line work. |
| Muted Text | Catalog Gray-Brown | `#62594c` | Metadata, captions, folios, secondary notes. |
| Rule | Faded Manila Rule | `#c9bca4` | Borders, dividers, cell outlines, quiet structure. |
| Accent | Oxide Red | `#b64127` | Byte IDs, important tags, active marks. |
| Accent 2 | Deep Signal Teal | `#145f68` | Section tags, signal elements, secondary emphasis. |
| Accent 3 | Tarnished Ochre | `#9b7a24` | Tertiary tags, checksum accents, ritual/detail marks. |
| Plate A | Cardboard Gold | `#d8c6a1` | Art placeholder base tone. |
| Plate B | Oxidized Green | `#9bb4b0` | Art placeholder secondary tone. |
| Plate C | Clay Orange | `#d7835b` | Art placeholder heat/accent tone. |

Core five excluding near-white: `#171512`, `#62594c`, `#c9bca4`, `#b64127`, `#145f68`.

## Dark Edition: Night Archive

| Role | Name | Hex | Use |
| --- | --- | --- | --- |
| Night Paper | Carbon Black | `#08090b` | Main page background. |
| Secondary Night | Charcoal Panel | `#121318` | Section pages, bands, plate backgrounds. |
| Ink | Warm Bone Ink | `#efe7d4` | Primary text and high-contrast marks. |
| Muted Text | Dust Gray | `#a89f8f` | Metadata, captions, folios, quieter notes. |
| Rule | Gunmetal Rule | `#353842` | Borders, dividers, memory-map cells. |
| Accent | Ember Orange | `#ff6a3d` | Byte IDs, active tags, urgent/system marks. |
| Accent 2 | Electric Patina | `#4db7c5` | Signal elements, secondary tags, machine glow. |
| Accent 3 | Brass Gold | `#d8ad3f` | Tertiary tags, checksum accents, ritual/detail marks. |
| Plate A | Bruised Violet | `#2a1f35` | Art placeholder base tone. |
| Plate B | Deep Teal Glass | `#123d45` | Art placeholder secondary tone. |
| Plate C | Burnt Umber | `#5b2a1e` | Art placeholder heat/accent tone. |

Core five excluding black: `#efe7d4`, `#a89f8f`, `#353842`, `#ff6a3d`, `#4db7c5`.

## Usage Rules

- Keep `paper` and `ink` dominant; accents should feel discovered, not decorative.
- Use `accent` for the primary byte/index signal.
- Use `accent_2` for transmissions, tags, and technical emphasis.
- Use `accent_3` sparingly for tertiary tags, checksum pages, and ritual or archive details.
- Use `rule` for structure before adding new colors.
- Use `plate_a`, `plate_b`, and `plate_c` only for generated art placeholders, art direction blocks, or section textures.
- Do not introduce pure white in the light edition or pure black as the only dark-edition background; both editions should retain the warmer archive tone.
- The light and dark editions should share the same color roles, so a reader can move between versions without relearning the system.
