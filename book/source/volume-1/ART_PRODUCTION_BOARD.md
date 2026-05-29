# Art Production Board

Status: draft production guide

## North Star

The book should feel like a box of impossible evidence: signal plates, archive cards, specimen studies, municipal scans, ritual diagrams, and broken machine records. The art should be tactile and specific, but not literal fan-art-style illustration.

## Batch 01: Manual Hero Art

Make these first. They define the commercial preview, cover options, section openers, and the first pass of the coffee-table feel.

- `00` The Static Between: opening transmission / cover candidate.
- `01` Frostbite Protocol: cold procedural specimen.
- `02` The Balcony Transmission: theater signal intrusion.
- `0B` Dock 12: dockside transmission.
- `20` The Bloom Protocol: soft-machine opener.
- `22` The Second Genome: biological foldout.
- `25` Backup Lectures: lecture packet with section dividers.
- `30` Tube 12's Complaint: municipal complaint opener.
- `37` Saved Fingerprint: biometric audit dossier.
- `40` The Nested Echo: analog echo.
- `50` The Between Places: threshold field report.
- `5B` The Spiral: spiral file.
- `60` The Ledger of Living Cells: body ledger opener.
- `70` Static Echoes: static memory.
- `90` The Mind in the Midway: maintenance carnival.
- `A0` The Memory Weaver of Elarion: large archive loom.
- `B0` The Echoes of Elsewhere: elsewhere physics opener.
- `B3` Dial-Up Entanglement: dial-up entanglement.
- `C0` The Unbroken Key: cryptographic relic opener.
- `C6` The Velvet Audit: soft audit record.
- `D0` Line Items in the Dark: underworld accounting.
- `E0` Tomorrow's Transfer: threshold transfer.
- `E1` The Receipt and the Rhododendron: final corrupted receipt.
- `E2` Final Notice: terminal notice.

## Batch 02: Auto-Draft Coverage

Generate draft plates for every remaining story using the `art-priority-queue.yaml` prompts. Drafts are not final art; they are composition fuel for page design.

## Batch 03: Manual Replacements

After a page-by-page review, replace any draft that feels generic, too literal, too synthetic, too busy, or too disconnected from the story.

## Asset Rules

- Store final assets outside git until rights and packaging are settled.
- Record provider, model, prompt, date, edit history, and human approver for every final image.
- Prefer abstract evidence, diagrams, scans, specimens, strange objects, and print textures over character scenes.
- Avoid recognizable brands, celebrity likenesses, franchise cues, living-artist style imitation, copied reference images, and legible protected text.
- Make alternate crops when the same piece needs to work on dark and light editions.

## Review Labels

- `missing`: no asset yet.
- `draft`: generated or sketched, not reviewed.
- `needs_human_review`: promising but not cleared.
- `approved_for_book`: cleared for paid publication.
- `rejected`: do not use.
