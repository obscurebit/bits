# Visual Design Reference Pass

Date: 2026-05-17

## References

- MoMA, "A Century of Artists Books": artist books treat the book itself as an art form, where writers, artists, and publishers transform the physical sequence of pages into the work.
- Fine Arts Museums of San Francisco, "The Book as Art": useful precedent for commingling visual elements with poetry and for making book structure, typography, and access systems part of the experience.
- Reedsy, "Creative Poetry Book Layouts": contemporary poetry-plus-image books tend to stay minimal when the artwork carries meaning; typography should be cohesive and intentional.
- DIY Book Formats, "How to Format & Publish a Poetry Book": poetry/prose book design depends heavily on line length, spacing, margins, and predictable vertical rhythm.
- TheCollector, "What Are Artist's Books?": artist books often sit at the intersection of art, graphic design, photography, printmaking, poetry, and object design.

## Design Translation For 256 Bits

- Treat each bit as a specimen page, not a uniform article template.
- Use story length to choose page density:
  - short stories get a larger art plate, a pull quote, and more negative space;
  - balanced stories get the standard art/text rhythm;
  - long stories get a compact plate and denser type, but no overflowing text columns.
- Keep text containers content-sized where possible. Decorative borders should not stretch through empty page space.
- Keep art plates as editorial objects: label, sigil, rule, texture, and mode-specific visual grammar.
- Reserve generated art for approved page-by-page prompts; store prompt/provider/rights state in `art_manifest.yaml`.

## Current Pass

- Added copy-density classes: `copy-airy`, `copy-balanced`, `copy-dense`.
- Enlarged plates for short pages and compacted them for dense pages.
- Added optional pull quotes for airy pages.
- Reworked art plates so labels sit like archival annotations instead of centered placeholder text.
- Prevented body decoration from stretching into dead space by aligning the body content to its own height.

## Second Pass Notes

- Increased excerpt budgets for long pages by layout mode so dense pages feel less abandoned.
- Replaced literal long QR URLs in page footers with compact edition metadata.
- Added an explicit "excerpt continues via QR" state when a page is intentionally partial.
- Removed CSS column properties entirely from story bodies; fixed-height multicolumn layout was creating hidden overflow columns.
- Kept protocol pages closer to form design by anchoring short protocol text near the top instead of vertically centering it.

## Third Pass Notes

- Added art placement classes so pages are not limited to a header plate:
  - `art-band`: horizontal plate above prose;
  - `art-side`: vertical sidecar art beside prose;
  - `art-inset`: smaller side/inset rectangle for compact form/archive pages;
  - `art-quad`: four-panel visual block for glitch-like entries.
- Kept DOM reading order consistent while using CSS grid areas to place visual blocks differently.
- Added a reusable quad-panel placeholder layer that can later be replaced by generated art crops.

## Fourth Pass Notes

- Added a print-prep fit pass that measures each rendered story body in the browser.
- If a story body overflows its page container, only that page's body type scales down, with a floor that prevents unreadably tiny text.
- Overflow-risk pages are flagged in DOM classes for QA, but the review PDFs do not show visible debug marks.
- This is a guardrail, not a substitute for art direction: high-value long pieces should still become two-page spreads when the fit scale gets too aggressive.

## Fifth Pass Notes

- Added selective two-page spreads for long-form entries at or above `700` words.
- Spread page one uses a larger art plate, pull quote, and opening excerpt; page two carries the continuation in a quieter reading layout.
- The spread budget currently allows up to `900` words, so the longest current selections can appear in full instead of ending as QR-only excerpts.
- Current review build promotes 10 entries into spreads, which keeps the book feeling curated without making every dense story use the same solution.

## Sixth Pass Notes

- Added explicit spread overrides for mid-length stories that should not be excerpted.
- Promoted bit `22`, "The Second Genome", into a two-page spread so the printed review includes the original ending.
- Fixed the spread splitter so once a story moves to the continuation page, all later blocks remain in order.

## Seventh Pass Notes

- Removed QR-only story excerpting from the normal book renderer; selected stories now continue onto additional pages until complete.
- Preserved source `---` dividers as visible story rules for sectioned pieces such as bit `25`, "Backup Lectures".
- Added compact transcript styling for divider-heavy pieces so they read like archival printouts.
- Filtered trailing generation scaffolding after dividers, such as prompt notes and image briefs, from the story body.
- Added a completion audit during review: every selected entry's final story tail is now present in generated HTML.

## Eighth Pass Notes

- Added mode-specific single-page limits so archive, signal, protocol, myth, field-note, and glitch stories make better spread decisions.
- Added hand-picked page budgets for stories that were producing weak continuation pages or isolated text scraps.
- Tightened no-teaser spread art plates so opener pages feel closer to art-book pacing and less like oversized placeholders.
- Reduced dense glitch typography and plate height to keep dossier-style pages compact without clipping.
- Updated continuation logic so glitch continuations stay text-led instead of receiving awkward tail art plates.
- Ran structural and browser overflow audits after the pass: no short final tails under 130 words and no measured body/page overflow in the review HTML.

## Ninth Pass Notes

- Replaced bottom-mounted closing plates on short continuation pages with sidecar closing plates, giving tails a stronger art-book composition.
- Promoted over-dense archive and glitch single pages into spreads by tightening archive and glitch single-page limits.
- Added story-specific opening budgets for newly promoted glitch pieces so their final pages do not become tiny orphan fragments.
- Restored glitch continuation pages to grid layout after the fit fixes, keeping footer and QR placement consistent.
- Rebuilt contact sheets for the full review book and re-ran browser overflow validation after the geometry changes.

## Tenth Pass Notes

- Added designed object pages: reader protocol, visual taxonomy, edition packet, mode index, theme index, generation map, art direction register, and colophon.
- Added `art_direction.yaml` as a production art registry with mode defaults and high-priority story treatments.
- Extended art plates with material/gesture notes, thread marks, orbit marks, and treatment-specific styling hooks.
- Reworked QR placeholders into small designed code labels so they read as part of the artifact system.
- Added front and back patterned endpapers using the 00-FF byte field, with filled addresses marking the current selected entries.

## Eleventh Pass Notes

- Reordered the closing sequence into a more physical book flow: catalog matter, release notes, certificate, back endpaper, back cover.
- Added a certificate of assembly page for future numbered/collector editions.
- Added a true back cover with sales-style copy, edition metadata, and a spine code.
- Added `GUMROAD_PACKET.md` to frame the product, included files, positioning, and release checklist.
