#!/usr/bin/env python3
"""Render designed review editions of the 256 Bits book."""

from __future__ import annotations

import argparse
import html
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

import book_build


DEFAULT_VOLUME_DIR = Path("book/volume-1")
DEFAULT_OUTPUT_DIR = Path("book-output/volume-1")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render artsy HTML/PDF review editions")
    parser.add_argument("--volume-dir", default=str(DEFAULT_VOLUME_DIR))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--theme", choices=["light", "dark", "both"], default="both")
    parser.add_argument("--format", choices=["html", "pdf", "both"], default="both")
    parser.add_argument("--allow-incomplete", action="store_true")
    return parser.parse_args()


def read_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text()) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Expected mapping in {path}")
    return data


def excerpt(text: str, words: int) -> str:
    clean = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        clean.append(stripped)
    tokens = " ".join(clean).split()
    if len(tokens) <= words:
        return " ".join(tokens)
    return " ".join(tokens[:words]).rstrip() + "..."


def section_title(manifest: dict[str, Any], code: str) -> str:
    for section in manifest.get("sections", []):
        if isinstance(section, dict) and str(section.get("code", "")).upper() == code.upper():
            return str(section.get("title", f"{code}x"))
    return f"{code}x"


def section_motif(manifest: dict[str, Any], code: str) -> str:
    for section in manifest.get("sections", []):
        if isinstance(section, dict) and str(section.get("code", "")).upper() == code.upper():
            return str(section.get("motif", ""))
    return ""


def section_tags(manifest: dict[str, Any], code: str) -> list[str]:
    motif = section_motif(manifest, code)
    tags: list[str] = []
    for part in motif.split(","):
        token = part.strip().lower()
        if not token:
            continue
        token = "".join(char if char.isalnum() else "-" for char in token)
        token = "-".join(filter(None, token.split("-")))
        if token:
            tags.append(f"#{token}")
    return tags


def section_entries(entries: list[book_build.BookEntry], code: str) -> list[book_build.BookEntry]:
    return [entry for entry in entries if entry.section_code.upper() == code.upper()]


def mode_info(design: dict[str, Any], mode: str) -> dict[str, str]:
    modes = design.get("layout_modes") or {}
    data = modes.get(mode) or {}
    return {
        "label": str(data.get("label", mode.replace("_", " ").title())),
        "glyph": str(data.get("glyph", mode[:3].upper())),
        "description": str(data.get("description", "")),
    }


def section_glyph(design: dict[str, Any], code: str) -> str:
    return str((design.get("section_glyphs") or {}).get(code.upper(), f"{code.upper()}x"))


def toc_cell(entry: book_build.BookEntry | None, index: int, design: dict[str, Any]) -> str:
    byte = f"{index:02X}"
    if not entry:
        return f'<div class="toc-cell empty"><strong>{byte}</strong></div>'
    mode = mode_info(design, entry.layout_mode)
    return (
        f'<div class="toc-cell {html.escape(entry.layout_mode)}">'
        f"<strong>{entry.byte_index}</strong><br>"
        f'<span class="toc-mode">{html.escape(mode["glyph"])}</span><br>'
        f"{html.escape(entry.bit.title)}</div>"
    )


def contents_grid(entries: list[book_build.BookEntry], design: dict[str, Any]) -> str:
    by_index = {int(entry.byte_index, 16): entry for entry in entries}
    return "\n".join(toc_cell(by_index.get(index), index, design) for index in range(256))


def mode_legend(design: dict[str, Any]) -> str:
    items = []
    for mode, data in (design.get("layout_modes") or {}).items():
        label = str(data.get("label", mode))
        glyph = str(data.get("glyph", mode[:3].upper()))
        items.append(f"<span>{html.escape(glyph)} {html.escape(label)}</span>")
    return '<div class="toc-legend">' + "".join(items) + "</div>"


def generation_label(entry: book_build.BookEntry) -> str:
    return f"gen:{entry.bit.generation_ref}" if entry.bit.generation_ref else ""


def section_strip(entries: list[book_build.BookEntry], code: str) -> str:
    by_offset = {int(entry.byte_index, 16) % 16: entry for entry in section_entries(entries, code)}
    cells: list[str] = []
    for offset in range(16):
        entry = by_offset.get(offset)
        if entry:
            cells.append(
                f'<div class="section-strip-cell filled">{entry.byte_index}<br>{html.escape(entry.layout_mode[:3].upper())}</div>'
            )
        else:
            cells.append('<div class="section-strip-cell"></div>')
    return '<div class="section-strip">' + "".join(cells) + "</div>"


def css_for(design: dict[str, Any], palette_name: str) -> str:
    palette = design["palettes"][palette_name]
    typo = design["typography"]
    trim = design["trim"]
    return f"""
@page {{
  size: {trim["width"]} {trim["height"]};
  margin: 0;
}}
* {{ box-sizing: border-box; }}
html, body {{
  margin: 0;
  background: {palette["paper"]};
  color: {palette["ink"]};
  font-family: {typo["body"]};
}}
.page {{
  width: {trim["width"]};
  min-height: {trim["height"]};
  break-after: page;
  page-break-after: always;
  position: relative;
  overflow: hidden;
  padding: {trim["margin_top"]} {trim["margin_outer"]} {trim["margin_bottom"]} {trim["margin_inner"]};
  background: {palette["paper"]};
}}
.page:nth-child(even) {{
  padding-left: {trim["margin_outer"]};
  padding-right: {trim["margin_inner"]};
}}
.cover {{
  display: grid;
  grid-template-rows: 1fr auto;
  background:
    linear-gradient(135deg, color-mix(in srgb, {palette["accent"]} 22%, transparent), transparent 42%),
    radial-gradient(circle at 72% 24%, {palette["accent_2"]} 0 8%, transparent 9%),
    linear-gradient(180deg, {palette["paper"]}, {palette["paper_alt"]});
}}
.cover-code {{
  font-family: {typo["mono"]};
  font-size: 9px;
  line-height: 1.65;
  color: {palette["muted"]};
  max-width: 2.2in;
  letter-spacing: 0.08em;
}}
.cover h1 {{
  font-family: {typo["display"]};
  font-size: 72px;
  line-height: 0.88;
  margin: 0 0 0.18in;
  font-weight: 400;
}}
.cover .subtitle {{
  font-family: {typo["sans"]};
  font-size: 13px;
  text-transform: uppercase;
  letter-spacing: 0.18em;
  color: {palette["muted"]};
}}
.cover .edition {{
  margin-top: 0.55in;
  font-family: {typo["mono"]};
  color: {palette["accent"]};
  font-size: 12px;
}}
.folio {{
  position: absolute;
  bottom: 0.22in;
  left: 0.52in;
  right: 0.52in;
  display: flex;
  justify-content: space-between;
  font-family: {typo["mono"]};
  font-size: 8px;
  color: {palette["muted"]};
  border-top: 1px solid {palette["rule"]};
  padding-top: 0.08in;
}}
.section {{
  display: grid;
  grid-template-rows: auto 1fr auto;
  background:
    linear-gradient(120deg, {palette["paper_alt"]}, {palette["paper"]} 62%),
    repeating-linear-gradient(90deg, transparent 0 9px, color-mix(in srgb, {palette["rule"]} 52%, transparent) 10px 11px);
}}
.section-mark {{
  font-family: {typo["mono"]};
  color: {palette["accent"]};
  font-size: 82px;
  line-height: 0.8;
}}
.section-glyph {{
  font-family: {typo["mono"]};
  color: {palette["accent_2"]};
  font-size: 12px;
  letter-spacing: 0.2em;
  text-transform: uppercase;
  margin-top: 0.12in;
}}
.section h2 {{
  font-family: {typo["display"]};
  font-size: 38px;
  line-height: 1;
  margin: 0.2in 0;
  font-weight: 400;
}}
.section-tags {{
  max-width: 4.9in;
  color: {palette["muted"]};
  font-family: {typo["mono"]};
  font-size: 13px;
  line-height: 1.55;
  letter-spacing: 0.02em;
}}
.section-tags span {{
  display: inline-block;
  margin: 0 0.09in 0.07in 0;
}}
.section-tags span:nth-child(3n + 1) {{
  color: {palette["accent"]};
}}
.section-tags span:nth-child(3n + 2) {{
  color: {palette["accent_2"]};
}}
.section-tags span:nth-child(3n) {{
  color: {palette["accent_3"]};
}}
.section-strip {{
  display: grid;
  grid-template-columns: repeat(16, 1fr);
  gap: 0.035in;
  align-self: end;
}}
.section-strip-cell {{
  min-height: 0.36in;
  border: 1px solid {palette["rule"]};
  padding: 0.035in;
  font-family: {typo["mono"]};
  font-size: 6px;
  color: {palette["muted"]};
  background: color-mix(in srgb, {palette["paper_alt"]} 72%, transparent);
}}
.section-strip-cell.filled {{
  color: {palette["ink"]};
  border-color: {palette["accent"]};
}}
.entry {{
  display: grid;
  grid-template-rows: auto 1fr auto;
  gap: 0.22in;
}}
.entry-head {{
  display: grid;
  grid-template-columns: 0.72in 1fr;
  gap: 0.18in;
  align-items: end;
}}
.byte {{
  font-family: {typo["mono"]};
  color: {palette["accent"]};
  font-size: 28px;
  line-height: 1;
}}
.entry h3 {{
  font-family: {typo["display"]};
  font-size: 30px;
  line-height: 1.02;
  margin: 0;
  font-weight: 400;
}}
.meta {{
  margin-top: 0.08in;
  font-family: {typo["sans"]};
  color: {palette["muted"]};
  text-transform: uppercase;
  letter-spacing: 0.08em;
  font-size: 8px;
}}
.gen-ref {{
  color: {palette["accent_2"]};
  font-family: {typo["mono"]};
  white-space: nowrap;
}}
.mode-badge {{
  display: inline-block;
  margin-top: 0.07in;
  font-family: {typo["mono"]};
  font-size: 7px;
  color: {palette["accent_2"]};
  letter-spacing: 0.16em;
  text-transform: uppercase;
}}
.entry-body {{
  font-size: 11.8px;
  line-height: 1.45;
  column-count: 2;
  column-gap: 0.24in;
}}
.entry.signal .entry-body {{
  font-family: {typo["mono"]};
  font-size: 9px;
  line-height: 1.55;
  column-count: 1;
  border-left: 2px solid {palette["accent_2"]};
  padding-left: 0.18in;
}}
.entry.archive .entry-body {{
  border-top: 1px solid {palette["rule"]};
  border-bottom: 1px solid {palette["rule"]};
  padding: 0.12in 0;
}}
.entry.field_note .entry-body {{
  background: repeating-linear-gradient(180deg, transparent 0 22px, color-mix(in srgb, {palette["rule"]} 48%, transparent) 23px 24px);
  padding: 0.06in 0.08in;
}}
.entry.protocol .entry-body {{
  font-family: {typo["sans"]};
  font-size: 10px;
  line-height: 1.5;
  column-count: 1;
  border: 1px solid {palette["rule"]};
  padding: 0.16in;
}}
.entry.myth .entry-body {{
  column-count: 1;
  font-size: 14px;
  line-height: 1.55;
}}
.entry.glitch .entry-body {{
  font-family: {typo["mono"]};
  font-size: 8.8px;
  line-height: 1.5;
  transform: skewY(-0.35deg);
  border: 1px dashed {palette["accent"]};
  padding: 0.13in;
}}
.plate {{
  min-height: 2.2in;
  border: 1px solid {palette["rule"]};
  background:
    radial-gradient(circle at 18% 24%, {palette["plate_c"]} 0 9%, transparent 10%),
    radial-gradient(circle at 82% 58%, {palette["plate_b"]} 0 13%, transparent 14%),
    linear-gradient(135deg, {palette["plate_a"]}, {palette["paper_alt"]});
  display: grid;
  place-items: center;
  position: relative;
}}
.entry.signal .plate {{
  background:
    repeating-linear-gradient(90deg, color-mix(in srgb, {palette["accent_2"]} 34%, transparent) 0 2px, transparent 3px 12px),
    linear-gradient(135deg, {palette["paper_alt"]}, {palette["plate_b"]});
}}
.entry.archive .plate {{
  background:
    linear-gradient(90deg, transparent 0 48%, color-mix(in srgb, {palette["ink"]} 18%, transparent) 49% 51%, transparent 52%),
    repeating-linear-gradient(180deg, {palette["plate_a"]} 0 15px, {palette["paper_alt"]} 16px 31px);
}}
.entry.protocol .plate {{
  background:
    linear-gradient(90deg, transparent 0 18%, color-mix(in srgb, {palette["rule"]} 74%, transparent) 18% 19%, transparent 20%),
    repeating-linear-gradient(180deg, {palette["paper_alt"]} 0 28px, color-mix(in srgb, {palette["accent"]} 12%, {palette["paper_alt"]}) 29px 30px);
}}
.entry.myth .plate {{
  background:
    radial-gradient(circle at 50% 50%, color-mix(in srgb, {palette["accent_3"]} 46%, transparent) 0 18%, transparent 19%),
    radial-gradient(circle at 50% 50%, transparent 0 34%, color-mix(in srgb, {palette["accent"]} 26%, transparent) 35% 36%, transparent 37%),
    linear-gradient(135deg, {palette["plate_a"]}, {palette["paper_alt"]});
}}
.entry.glitch .plate {{
  background:
    linear-gradient(90deg, transparent 0 18%, color-mix(in srgb, {palette["accent"]} 45%, transparent) 19% 22%, transparent 23% 61%, color-mix(in srgb, {palette["accent_2"]} 40%, transparent) 62% 65%, transparent 66%),
    repeating-linear-gradient(180deg, {palette["paper_alt"]} 0 6px, {palette["plate_c"]} 7px 8px);
}}
.plate::before {{
  content: "";
  position: absolute;
  inset: 0.14in;
  border: 1px solid color-mix(in srgb, {palette["ink"]} 18%, transparent);
}}
.plate-label {{
  font-family: {typo["mono"]};
  color: color-mix(in srgb, {palette["ink"]} 62%, transparent);
  font-size: 10px;
  letter-spacing: 0.16em;
  text-transform: uppercase;
  z-index: 1;
}}
.entry-foot {{
  display: grid;
  grid-template-columns: 1fr 0.54in;
  gap: 0.18in;
  align-items: end;
}}
.caption {{
  color: {palette["muted"]};
  font-family: {typo["sans"]};
  font-size: 8.6px;
  line-height: 1.35;
}}
.qr {{
  width: 0.54in;
  height: 0.54in;
  border: 1px solid {palette["ink"]};
  display: grid;
  place-items: center;
  font-family: {typo["mono"]};
  font-size: 9px;
  color: {palette["ink"]};
  background:
    linear-gradient(90deg, transparent 42%, {palette["ink"]} 43% 57%, transparent 58%),
    linear-gradient(0deg, transparent 42%, {palette["ink"]} 43% 57%, transparent 58%);
}}
.toc h2, .notes h2 {{
  font-family: {typo["display"]};
  font-size: 34px;
  font-weight: 400;
}}
.toc {{
  height: {trim["height"]};
  display: grid;
  grid-template-rows: auto auto 1fr;
  gap: 0.08in;
  padding-bottom: 0.74in;
}}
.toc h2 {{
  margin: 0;
  line-height: 0.95;
}}
.front-note h2 {{
  font-family: {typo["display"]};
  font-size: 36px;
  line-height: 1.02;
  font-weight: 400;
  margin: 0 0 0.32in;
}}
.front-note .note-body {{
  font-size: 15px;
  line-height: 1.48;
  max-width: 4.9in;
}}
.front-note .note-kicker {{
  font-family: {typo["mono"]};
  font-size: 9px;
  color: {palette["accent"]};
  text-transform: uppercase;
  letter-spacing: 0.16em;
  margin-bottom: 0.3in;
}}
.toc-grid {{
  display: grid;
  grid-template-columns: repeat(16, 1fr);
  grid-template-rows: repeat(16, minmax(0, 1fr));
  gap: 0.026in;
  font-family: {typo["mono"]};
  font-size: 5.1px;
  line-height: 1.08;
  min-height: 0;
}}
.toc-cell {{
  border: 1px solid color-mix(in srgb, {palette["rule"]} 76%, transparent);
  padding: 0.026in;
  overflow: hidden;
  background: color-mix(in srgb, {palette["paper_alt"]} 68%, transparent);
}}
.toc-cell strong {{
  color: {palette["accent"]};
  font-size: 7px;
}}
.toc-cell.empty {{
  opacity: 0.32;
}}
.toc-cell .toc-mode {{
  color: {palette["accent_2"]};
  font-size: 5px;
  text-transform: uppercase;
}}
.toc-legend {{
  display: flex;
  flex-wrap: wrap;
  gap: 0.045in 0.11in;
  margin: 0;
  font-family: {typo["mono"]};
  color: {palette["muted"]};
  font-size: 7px;
  text-transform: uppercase;
  letter-spacing: 0.08em;
}}
.muted {{ color: {palette["muted"]}; }}
"""


def load_front_note(path: Path, fallback_title: str) -> tuple[str, str, str]:
    if not path.exists():
        return fallback_title, "missing", "This required front-matter note has not been written yet."
    raw = path.read_text()
    front, body = book_build.parse_frontmatter(raw)
    title = fallback_title
    paragraphs: list[str] = []
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            title = stripped[2:].strip()
            continue
        if stripped:
            paragraphs.append(stripped)
    return title, front.get("status", "unknown"), "\n\n".join(paragraphs)


def front_matter_pages(manifest: dict[str, Any], design: dict[str, Any]) -> list[str]:
    pages: list[str] = []
    front_matter = manifest.get("front_matter") or {}
    note_words = int(design.get("layout", {}).get("front_note_excerpt_words", 150))
    for key in ["creator_note", "machines_note", "editor_letter"]:
        config = front_matter.get(key)
        if not isinstance(config, dict):
            continue
        source = Path(str(config.get("source", "")))
        title, status, body = load_front_note(source, str(config.get("title", key.replace("_", " ").title())))
        pages.extend(
            [
                '<section class="page front-note">',
                f'<div class="note-kicker">{html.escape(status)} / front matter</div>',
                f"<h2>{html.escape(title)}</h2>",
                f'<div class="note-body">{html.escape(excerpt(body, note_words))}</div>',
                folio(key.replace("_", " "), "front"),
                "</section>",
            ]
        )
    return pages


def render_html(
    entries: list[book_build.BookEntry],
    warnings: list[str],
    manifest: dict[str, Any],
    design: dict[str, Any],
    palette_name: str,
) -> str:
    palette = design["palettes"][palette_name]
    excerpt_words = int(design.get("layout", {}).get("entry_excerpt_words", 220))
    parts = [
        "<!doctype html>",
        "<html>",
        "<head>",
        '<meta charset="utf-8">',
        f"<title>{html.escape(str(manifest.get('title', '256 Bits')))} - {palette_name}</title>",
        "<style>",
        css_for(design, palette_name),
        "</style>",
        "</head>",
        "<body>",
        '<section class="page cover">',
        '<div class="cover-code">00 01 02 03 04 05 06 07<br>08 09 0A 0B 0C 0D 0E 0F<br><br>10 11 12 13 14 15 16 17<br>18 19 1A 1B 1C 1D 1E 1F</div>',
        "<div>",
        f"<h1>{html.escape(str(manifest.get('title', '256 Bits')))}</h1>",
        f'<div class="subtitle">{html.escape(str(manifest.get("subtitle", "Volume 1")))}</div>',
        f'<div class="edition">{html.escape(str(manifest.get("edition_name", "Review Edition")))} / {html.escape(palette["name"])}</div>',
        "</div>",
        "</section>",
        *front_matter_pages(manifest, design),
        '<section class="page toc">',
        "<h2>Memory Map</h2>",
        mode_legend(design),
        '<div class="toc-grid">',
        contents_grid(entries, design),
    ]
    parts.extend(["</div>", folio("contents / 00-FF", palette_name), "</section>"])

    current_section = None
    page_num = 2
    for entry in entries:
        if entry.section_code != current_section:
            current_section = entry.section_code
            page_num += 1
            parts.extend(
                [
                    '<section class="page section">',
                    "<div>",
                    f'<div class="section-mark">{html.escape(current_section)}x</div>',
                    f'<div class="section-glyph">{html.escape(section_glyph(design, current_section))} / {len(section_entries(entries, current_section)):02d} entries</div>',
                    f"<h2>{html.escape(section_title(manifest, current_section))}</h2>",
                    '<div class="section-tags">'
                    + "".join(f"<span>{html.escape(tag)}</span>" for tag in section_tags(manifest, current_section))
                    + "</div>",
                    "</div>",
                    section_strip(entries, current_section),
                    folio(f"section {current_section}", str(page_num)),
                    "</section>",
                ]
            )
        page_num += 1
        body = html.escape(excerpt(entry.bit.body, excerpt_words))
        mode = mode_info(design, entry.layout_mode)
        gen_label = generation_label(entry)
        gen_meta = f' / <span class="gen-ref">{html.escape(gen_label)}</span>' if gen_label else ""
        caption_lines = [
            f"QR target: {html.escape(entry.qr_target)}",
            "Draft plate only. Final art requires provider, prompt, rights note, and human approval.",
        ]
        if gen_label:
            caption_lines.insert(1, f"Generation source: {html.escape(gen_label)}")
        parts.extend(
            [
                f'<section class="page entry {html.escape(entry.layout_mode)}">',
                '<div class="entry-head">',
                f'<div class="byte">{entry.byte_index}</div>',
                "<div>",
                f"<h3>{html.escape(entry.bit.title)}</h3>",
                f'<div class="meta">{html.escape(entry.bit.date)} / {html.escape(entry.bit.theme or "unmarked")} / {html.escape(section_title(manifest, entry.section_code))}{gen_meta}</div>',
                f'<div class="mode-badge">{html.escape(mode["glyph"])} / {html.escape(mode["label"])}</div>',
                "</div>",
                "</div>",
                '<div class="plate">',
                f'<div class="plate-label">{html.escape(mode["label"])} Plate / {html.escape(entry.art_lane)} / {html.escape(entry.art_status)}</div>',
                "</div>",
                f'<div class="entry-body">{body}</div>',
                '<div class="entry-foot">',
                f'<div class="caption">{"<br>".join(caption_lines)}</div>',
                '<div class="qr">QR</div>',
                "</div>",
                folio(entry.byte_index, str(page_num)),
                "</section>",
            ]
        )

    parts.extend(
        [
            '<section class="page notes">',
            "<h2>Release Notes</h2>",
            f'<p class="muted">This review edition contains {len(entries)} selected entries. It is intentionally incomplete until the volume reaches 256 approved bits.</p>',
            "<ul>",
        ]
    )
    for warning in warnings[:24]:
        parts.append(f"<li>{html.escape(warning)}</li>")
    if len(warnings) > 24:
        parts.append(f"<li>{len(warnings) - 24} additional blockers in validation-report.md</li>")
    parts.extend(["</ul>", folio("validation", "end"), "</section>", "</body>", "</html>"])
    return "\n".join(parts)


def folio(left: str, right: str) -> str:
    return f'<div class="folio"><span>{html.escape(left)}</span><span>{html.escape(right)}</span></div>'


def chrome_path() -> str | None:
    candidates = [
        shutil.which("chromium"),
        shutil.which("google-chrome"),
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    return None


def print_pdf(html_path: Path, pdf_path: Path) -> None:
    chrome = chrome_path()
    if not chrome:
        raise RuntimeError("Chrome/Chromium not found; HTML was rendered but PDF cannot be printed.")
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        chrome,
        "--headless",
        "--disable-background-networking",
        "--disable-component-update",
        "--disable-crash-reporter",
        "--disable-default-apps",
        "--disable-gpu",
        "--disable-sync",
        "--metrics-recording-only",
        "--no-first-run",
        "--no-pdf-header-footer",
        "--run-all-compositor-stages-before-draw",
        f"--user-data-dir={Path('/private/tmp/obscurebit-chrome-profile')}",
        f"--print-to-pdf={pdf_path.resolve()}",
        html_path.resolve().as_uri(),
    ]
    try:
        subprocess.run(command, check=True, timeout=45)
    except subprocess.TimeoutExpired:
        if pdf_path.exists() and pdf_path.stat().st_size > 0:
            return
        raise


def render(volume_dir: Path, output_dir: Path, palette_name: str, formats: set[str]) -> tuple[Path, Path | None, list[str]]:
    manifest = book_build.read_yaml(volume_dir / "manifest.yaml")
    design = read_yaml(volume_dir / "design.yaml")
    art_entries = book_build.load_art_entries(volume_dir / "art_manifest.yaml")
    posts = book_build.discover_bit_posts()
    entries, warnings = book_build.build_entries(manifest, posts, art_entries)
    name_blockers, name_warnings = book_build.validate_name_collisions(entries, manifest)
    warnings = book_build.validate_front_matter(manifest) + warnings
    warnings.extend(name_blockers)
    warnings.extend(name_warnings)

    output_dir.mkdir(parents=True, exist_ok=True)
    html_path = output_dir / f"256-bits-volume-1-{palette_name}-review.html"
    pdf_path = output_dir / f"256-bits-volume-1-{palette_name}-review.pdf"
    html_path.write_text(render_html(entries, warnings, manifest, design, palette_name))

    rendered_pdf = None
    if "pdf" in formats:
        print_pdf(html_path, pdf_path)
        rendered_pdf = pdf_path
    return html_path, rendered_pdf, warnings


def main() -> None:
    args = parse_args()
    theme_names = ["light", "dark"] if args.theme == "both" else [args.theme]
    formats = {"html", "pdf"} if args.format == "both" else {args.format}

    all_warnings: list[str] = []
    for theme_name in theme_names:
        html_path, pdf_path, warnings = render(Path(args.volume_dir), Path(args.output_dir), theme_name, formats)
        all_warnings.extend(warnings)
        print(f"Rendered {theme_name} HTML: {html_path}")
        if pdf_path:
            print(f"Rendered {theme_name} PDF: {pdf_path}")

    if all_warnings and not args.allow_incomplete:
        for warning in all_warnings[:12]:
            print(f"WARNING: {warning}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
