# Font Plan

The book should use a small open-source type system that feels archival, technical, and slightly uncanny without becoming novelty typography.

## Selected Stack

This is the recommended production stack:

- `Cormorant Garamond` for the artifact voice.
- `Source Serif 4` for sustained reading.
- `Space Grotesk` for contemporary labels and metadata.
- `IBM Plex Mono` for byte indexes, QR labels, checks, and machine/editorial apparatus.

The combination should feel literary, archival, precise, and slightly uncanny. The serif/sans split keeps the pages from turning into pure nostalgia, while the mono layer gives the `00` through `FF` structure a real visual language.

## Wider Search Shortlist

These are free/open candidates worth auditioning before we lock the release PDF. The goal is not to use all of them; it is to choose the few that make the book feel intentional.

### Strong Additions To Audition

#### Recursive Sans & Mono

Best use: section theme tags, machine/editor notes, the memory map, and any page that wants a programmer feel with personality.

Why it fits: Recursive has both sans and mono modes, plus a casual-to-linear expression axis. That makes it unusually good for this book: it can feel technical, but not sterile. It is the most interesting candidate for replacing or supplementing IBM Plex Mono on section pages.

Source: <https://www.recursive.design/>

Project: <https://github.com/arrowtype/recursive>

License note: Recursive is licensed under the SIL Open Font License 1.1.

Verdict: audition as the hacker/editorial accent font.

#### JetBrains Mono

Best use: code-like tags, byte tables, validation stamps, QR labels, and dark-edition machine details.

Why it fits: it is explicitly designed for developers, has good character distinction, and supports code ligatures. It feels more contemporary-programmer than IBM Plex Mono.

Source: <https://www.jetbrains.com/lp/mono/>

Project: <https://github.com/JetBrains/JetBrainsMono>

License note: JetBrains says the typeface is available under SIL Open Font License 1.1 and can be used free of charge for commercial and non-commercial purposes.

Verdict: strong alternate for the byte layer if we want more obvious developer energy.

#### Source Code Pro

Best use: validation reports, metadata tables, build notes, and colophon/system pages.

Why it fits: it is restrained, professional, and made for UI/code environments. Less personality than JetBrains Mono or Recursive, but very dependable in print.

Source: <https://github.com/adobe-fonts/source-code-pro>

License note: the Adobe repository is OFL-1.1.

Verdict: use if IBM Plex Mono feels too branded or JetBrains Mono feels too IDE-like.

#### Atkinson Hyperlegible Mono / Next

Best use: QR instructions, accessibility-minded metadata, small labels, and possibly the Gumroad/readme materials.

Why it fits: made for character distinction and readability. It is less mysterious, more humane and accessible.

Source: <https://www.brailleinstitute.org/freefont/>

License note: Braille Institute describes the family as free for personal use and all commercial applications.

Verdict: best accessibility alternate, especially for tiny print.

#### Fraunces

Best use: occasional section display, pull quotes, or art-plate captions when we want a warmer uncanny note.

Why it fits: a wonky serif with variable range and a little vintage oddness. More playful than Cormorant, less solemn.

Source: <https://fonts.google.com/specimen/Fraunces>

Project: <https://github.com/undercasetype/Fraunces>

License note: Fraunces is distributed under the SIL Open Font License.

Verdict: optional display alternate; use sparingly.

#### Unbounded

Best use: cover marks, divider typography, checksum pages, promotional graphics.

Why it fits: futuristic, wide, and object-like. It can make `256 Bits` feel more like an artifact from a system than a literary collection.

Source: <https://fonts.google.com/specimen/Unbounded>

Project: <https://github.com/w3f/unbounded>

License note: the repository includes `OFL.txt` and identifies the font as SIL Open Font License 1.1.

Verdict: strong for cover/detail experiments, too loud for body or normal section titles.

#### Syne

Best use: experimental title treatments, art labels, and promotional graphics.

Why it fits: art-institution energy. More cultural-poster than hacker, which could help the book feel curated.

Source: <https://fonts.google.com/specimen/Syne>

License note: Syne is available as an open font through Google Fonts and commonly distributed under OFL.

Verdict: audition for marketing/cover, not body.

#### Libre Baskerville

Best use: conservative body alternate, creator note, or front matter.

Why it fits: stable, literary, and familiar. It is less strange than Source Serif 4, but very readable.

Source: <https://fonts.google.com/specimen/Libre+Baskerville>

License note: Libre Baskerville is distributed under the SIL Open Font License.

Verdict: backup body serif if Source Serif 4 feels too modern.

#### Literata

Best use: ebook-first body alternate and long-form story text.

Why it fits: designed for sustained reading, with a contemporary book feel. It has less eerie atmosphere than Source Serif 4 but may perform well in digital editions.

Source: <https://fonts.google.com/specimen/Literata>

Verdict: audition only if the PDF body text needs more ebook warmth.

#### Noto Serif

Best use: fallback/global coverage, appendix, generated indexes, or multilingual edge cases.

Why it fits: huge coverage and dependable rendering. It is not as distinctive, but it is a useful safety net.

Source: <https://fonts.google.com/noto/specimen/Noto+Serif>

Verdict: fallback/support font, not the visual voice.

### Candidates To Avoid For This Volume

- `Major Mono Display`: fun, but too novelty for sustained use. Could work for one poster-like promo, not the book system.
- `Hack`: practical code font, but visually too plain for an art book.
- `Iosevka`: excellent technical font, but too compressed/engineering-forward for our current page tone.
- Any paid-only "hacker" face: unnecessary licensing risk when Recursive, JetBrains Mono, and Source Code Pro cover the space well.

## Recommended Audition Sets

### Set A: Current Editorial Machine

- Display: `Cormorant Garamond`
- Body: `Source Serif 4`
- Sans: `Space Grotesk`
- Mono: `IBM Plex Mono`

This is the safest current stack: literary, restrained, and coherent.

### Set B: Stranger Programmer Artifact

- Display: `Cormorant Garamond`
- Body: `Source Serif 4`
- Sans/Tags: `Recursive Sans`
- Mono: `Recursive Mono`
- Optional cover/detail: `Unbounded`

This is my favorite next audition. It gives the section pages and memory map more life without adding a completely separate novelty font.

### Set C: Clean Developer Edition

- Display: `Cormorant Garamond`
- Body: `Source Serif 4`
- Sans: `Space Grotesk`
- Mono: `JetBrains Mono`
- Utility/accessibility: `Atkinson Hyperlegible`

This reads more explicitly as software/developer culture. Good for dark mode, validation pages, and QR-heavy pages.

### Set D: Warmer Art Book

- Display: `Fraunces`
- Body: `Source Serif 4` or `Literata`
- Sans: `Space Grotesk`
- Mono: `IBM Plex Mono` or `Source Code Pro`

This is softer and more gallery-book-ish, but it risks losing some of the strange technical bite.

### Display: Cormorant Garamond

Use for cover title, section titles, large entry titles, and occasional pull text.

Why it fits: literary, old-world, sharp, a little strange. It gives the book the cabinet-of-curiosities feel without drifting into fake antique styling.

Source: <https://fonts.google.com/specimen/Cormorant+Garamond>

Project: <https://github.com/CatharsisFonts/Cormorant>

License note: Cormorant is distributed under the SIL Open Font License. The project repository includes `OFL.txt`, and Adobe Fonts lists Cormorant Garamond as open source and cleared for commercial use in Adobe Fonts workflows.

Recommended weights:

- Regular 400 for large titles.
- Italic 400 for short pull fragments and dreamlike accents.
- SemiBold 600 for section title emphasis only.

### Body: Source Serif 4

Use for story text, notes, essays, captions that need warmth, and long-form prose.

Why it fits: serious editorial serif with optical-size thinking. It can carry long reading pages more calmly than Cormorant, and it keeps the book from becoming too theatrical.

Source: <https://fonts.google.com/specimen/Source+Serif+4>

Project: <https://github.com/adobe-fonts/source-serif>

License note: Adobe Fonts describes Source Serif 4 as open source, with PDF embedding allowed in Adobe Fonts workflows. The Adobe source repository is under OFL-1.1.

Recommended weights:

- Regular 400 for all story bodies.
- Italic 400 for quoted notes, marginalia, and front matter accents.
- Semibold 600 for small essay headings if needed.

### Sans/Metadata: Space Grotesk

Use for metadata, labels, section legends, status lines, validation stamps, and small UI-like details.

Why it fits: technical but not cold. It keeps the book contemporary and readable, especially around the byte-grid and Gumroad/digital-edition surfaces.

Source: <https://fonts.google.com/specimen/Space+Grotesk>

Project: <https://github.com/floriankarsten/space-grotesk>

License note: Space Grotesk is distributed under the SIL Open Font License.

Recommended weights:

- Regular 400 for captions and legends.
- Medium 500 for metadata labels.
- Semibold 600 for section codes and small navigation marks.

### Mono/Byte Layer: IBM Plex Mono

Use for byte indexes, QR labels, validation reports, machine voice, code-ish front matter, and the `00` through `FF` grid.

Why it fits: credible machine typography, less gimmicky than retro terminal fonts, readable at tiny sizes.

Source: <https://fonts.google.com/specimen/IBM+Plex+Mono>

Project: <https://github.com/IBM/plex>

License note: IBM's Plex repository is open source under the SIL Open Font License, Version 1.1.

Recommended weights:

- Regular 400 for byte IDs, QR captions, and validation notes.
- Semibold 600 for cover codes, section glyphs, and checksum-like marks.

## Rejected Directions

- `Cinzel`, `Uncial Antiqua`, or inscriptional caps: too fantasy-coded.
- Script handwriting fonts: too much fake intimacy; the creator note should feel authored by layout and language, not simulated handwriting.
- Retro terminal fonts: too nostalgic and predictable for the machine layer.
- One-family systems: too flat for an art book that needs human/editor/machine strata.

## Typesetting Rules

- Story text should stay mostly Source Serif 4 at 10.5-12 pt equivalent, with generous leading.
- Entry titles can use Cormorant large, but not bold; the weirdness should come from form and spacing, not shouting.
- Labels should use Space Grotesk in small caps or uppercase sparingly.
- Byte numbers should be IBM Plex Mono with tabular alignment.
- Letter spacing is allowed only for tiny metadata, cover code, and section marks.
- The dark and light editions should share fonts exactly; only paper, ink, accent, and art treatment should change.

## Implementation Notes

The design file now names the intended open fonts, with system fallbacks for local review PDFs:

- `display`: Cormorant Garamond
- `body`: Source Serif 4
- `sans`: Space Grotesk
- `mono`: IBM Plex Mono

Before the commercial release, we should self-host the exact font files under a private or committed font asset directory and include license files. Google Fonts links are fine for exploration, but release builds should not depend on live network font loading.

Recommended future asset layout:

```text
book/volume-1/fonts/
  CormorantGaramond/
    OFL.txt
    CormorantGaramond-Regular.ttf
    CormorantGaramond-SemiBold.ttf
    CormorantGaramond-Italic.ttf
  SourceSerif4/
    OFL.txt
    SourceSerif4-Regular.ttf
    SourceSerif4-Italic.ttf
    SourceSerif4-Semibold.ttf
  SpaceGrotesk/
    OFL.txt
    SpaceGrotesk-Regular.ttf
    SpaceGrotesk-SemiBold.ttf
  IBMPlexMono/
    OFL.txt
    IBMPlexMono-Regular.ttf
    IBMPlexMono-SemiBold.ttf
```

## Tone Rules

- Cormorant should be used large and sparingly.
- Source Serif should do the actual reading work.
- Space Grotesk should label systems, not become the book's voice.
- IBM Plex Mono should be the machine/index layer, never the whole book.
- Avoid decorative script fonts and faux-typewriter fonts.
