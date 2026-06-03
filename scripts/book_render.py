#!/usr/bin/env python3
"""Render designed review editions of the 256 Bits book."""

from __future__ import annotations

import argparse
import base64
import html
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import yaml
import segno

import book_build


DEFAULT_VOLUME_DIR = Path("book/source/volume-1")
DEFAULT_OUTPUT_DIR = Path("book/output/volume-1")
LOGO_MARK_PATH = Path("assets/obscure-bit-mark.png")
ART_DIRECTION_PATH = Path("art_direction.yaml")
STORY_DIVIDER = "__OBSCUREBIT_STORY_DIVIDER__"
PDF_IMAGE_PROFILES: dict[str, dict[str, Any]] = {
    "review": {"suffix": "review", "quality": None, "max_long_edge": None, "optimize_images": False},
    "print": {"suffix": "print", "quality": 90, "max_long_edge": None, "optimize_images": True},
    "download": {"suffix": "download", "quality": 45, "max_long_edge": 700, "optimize_images": True},
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render artsy HTML/PDF review editions")
    parser.add_argument("--volume-dir", default=str(DEFAULT_VOLUME_DIR))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--theme", choices=["light", "dark", "both"], default="both")
    parser.add_argument("--format", choices=["html", "pdf", "both"], default="both")
    parser.add_argument(
        "--pdf-profile",
        choices=sorted(PDF_IMAGE_PROFILES),
        default="review",
        help="Image profile and output suffix for PDF/HTML render assets.",
    )
    parser.add_argument(
        "--asset-mode",
        choices=["auto", "inline", "linked"],
        default="auto",
        help="Use inline data URIs or linked file URLs. Auto inlines review renders and links optimized PDF profiles.",
    )
    parser.add_argument("--allow-incomplete", action="store_true")
    return parser.parse_args()


def read_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text()) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Expected mapping in {path}")
    return data


def read_optional_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return read_yaml(path)


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


def clean_excerpt_boundary(text: str) -> str:
    stripped = text.rstrip()
    if not stripped:
        return stripped
    boundary = max(stripped.rfind("."), stripped.rfind("?"), stripped.rfind("!"))
    if boundary >= max(36, int(len(stripped) * 0.55)):
        return stripped[: boundary + 1]
    return stripped + "..."


def excerpt_paragraphs_html(text: str, words: int) -> str:
    paragraphs: list[str] = []
    current: list[str] = []
    used = 0
    truncated = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if not stripped:
            if current:
                paragraphs.append(" ".join(current))
                current = []
            continue
        tokens = stripped.split()
        remaining = words - used
        if remaining <= 0:
            truncated = True
            break
        if len(tokens) > remaining:
            if paragraphs or current:
                truncated = True
                break
            current.append(clean_excerpt_boundary(" ".join(tokens[:remaining])))
            used += remaining
            truncated = True
            break
        current.append(stripped)
        used += len(tokens)
    if current:
        paragraphs.append(" ".join(current))
    if not paragraphs:
        paragraphs.append("Awaiting final copy.")
    if truncated and not paragraphs[-1].endswith(("...", ".", "?", "!")):
        paragraphs[-1] = paragraphs[-1].rstrip() + "..."
    return "".join(f"<p>{html.escape(paragraph)}</p>" for paragraph in paragraphs)


def is_generation_artifact_heading(text: str) -> bool:
    normalized = text.strip().lower()
    normalized = normalized.strip("[](){} ")
    normalized = re.sub(r"^\*\*([^*]+)\*\*", r"\1", normalized)
    normalized = normalized.replace("**", "")
    normalized = normalized.lstrip("*").strip()
    artifact_prefixes = (
        "note:",
        "image:",
        "strange image:",
        "stranger image:",
        "funny line:",
        "emotional turn:",
        "unpredictable sentence:",
        "unpredicted sentence:",
        "speculative inconvenience:",
        "fresh detail:",
        "breakthrough ",
        "final annotation:",
        "refusal:",
    )
    return normalized.startswith(artifact_prefixes)


def story_blocks(text: str, words: int, include_dividers: bool = False) -> list[str]:
    blocks: list[str] = []
    remaining = words
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        index += 1
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if is_generation_artifact_heading(stripped):
            continue
        if stripped == "---":
            next_content = ""
            for future_line in lines[index:]:
                future_stripped = future_line.strip()
                if future_stripped:
                    next_content = future_stripped
                    break
            if next_content and is_generation_artifact_heading(next_content):
                break
            if include_dividers and blocks and blocks[-1] != STORY_DIVIDER:
                blocks.append(STORY_DIVIDER)
            continue
        tokens = stripped.split()
        if not tokens:
            continue
        if len(tokens) > remaining:
            blocks.append(" ".join(tokens[:remaining]).rstrip() + "...")
            break
        blocks.append(stripped)
        remaining -= len(tokens)
        if remaining <= 0:
            break
    return blocks


def inline_markdown(text: str) -> str:
    escaped = html.escape(text)
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"\*([^*]+)\*", r"<em>\1</em>", escaped)
    return escaped


def image_data_uri(path: Path) -> str:
    if not path.exists():
        return ""
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    mime_type = mimetypes.guess_type(path.name)[0] or "image/png"
    return f"data:{mime_type};base64,{encoded}"


class AssetResolver:
    def __init__(self, volume_dir: Path, output_dir: Path, profile_name: str, asset_mode: str) -> None:
        self.volume_dir = volume_dir.resolve()
        self.assets_root = (self.volume_dir / "assets").resolve()
        self.output_dir = output_dir.resolve()
        self.profile_name = profile_name
        self.profile = PDF_IMAGE_PROFILES[profile_name]
        if asset_mode == "auto":
            asset_mode = "inline" if profile_name == "review" else "linked"
        self.inline = asset_mode == "inline"
        max_edge = self.profile.get("max_long_edge")
        max_label = str(max_edge) if max_edge else "native"
        quality = self.profile.get("quality") or "source"
        self.cache_dir = self.output_dir / "image-cache" / f"{profile_name}-q{quality}-{max_label}"

    def uri(self, path: Path) -> str:
        resolved = path if path.is_absolute() else Path.cwd() / path
        resolved = resolved.resolve()
        if not resolved.exists():
            return ""
        if self.inline:
            return image_data_uri(resolved)
        link_path = self.optimized_asset_path(resolved)
        return link_path.as_uri()

    def optimized_asset_path(self, path: Path) -> Path:
        if not self.should_optimize(path):
            return path
        rel = path.relative_to(self.assets_root)
        target = (self.cache_dir / rel).with_suffix(".jpg")
        if target.exists() and target.stat().st_mtime >= path.stat().st_mtime:
            return target
        target.parent.mkdir(parents=True, exist_ok=True)
        self.convert_to_jpeg(path, target)
        return target

    def should_optimize(self, path: Path) -> bool:
        if not self.profile.get("optimize_images"):
            return False
        if path.name == LOGO_MARK_PATH.name:
            return False
        if path.suffix.lower() not in {".png", ".jpg", ".jpeg"}:
            return False
        try:
            path.relative_to(self.assets_root)
        except ValueError:
            return False
        return True

    def convert_to_jpeg(self, source: Path, target: Path) -> None:
        quality = int(self.profile.get("quality") or 90)
        max_long_edge = self.profile.get("max_long_edge")
        if self.convert_with_pillow(source, target, quality, max_long_edge):
            return
        if self.convert_with_sips(source, target, quality, max_long_edge):
            return
        raise RuntimeError(
            f"Cannot optimize {source}; install Pillow or run on macOS with sips available."
        )

    def convert_with_pillow(self, source: Path, target: Path, quality: int, max_long_edge: Any) -> bool:
        try:
            from PIL import Image
        except ImportError:
            return False
        with Image.open(source) as image:
            image = image.convert("RGB")
            if max_long_edge and max(image.size) > int(max_long_edge):
                ratio = int(max_long_edge) / max(image.size)
                size = (round(image.width * ratio), round(image.height * ratio))
                resampling = getattr(getattr(Image, "Resampling", Image), "LANCZOS")
                image = image.resize(size, resampling)
            image.save(target, "JPEG", quality=quality, optimize=True, progressive=True, subsampling=1)
        return True

    def convert_with_sips(self, source: Path, target: Path, quality: int, max_long_edge: Any) -> bool:
        sips = shutil.which("sips")
        if not sips:
            return False
        temp_target = target.with_suffix(".tmp.jpg")
        command = [sips]
        if max_long_edge:
            command.extend(["-Z", str(int(max_long_edge))])
        command.extend(
            [
                "-s",
                "format",
                "jpeg",
                "-s",
                "formatOptions",
                str(quality),
                str(source),
                "--out",
                str(temp_target),
            ]
        )
        subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        temp_target.replace(target)
        return True


def render_story_body(text: str, words: int) -> str:
    blocks = story_blocks(text, words)
    return render_story_blocks(blocks)


def render_story_blocks(blocks: list[str]) -> str:
    rendered: list[str] = []
    for block in blocks:
        if block == STORY_DIVIDER:
            rendered.append('<hr class="story-divider">')
        else:
            rendered.append(f"<p>{inline_markdown(block)}</p>")
    return "".join(rendered)


def body_word_count(text: str) -> int:
    return sum(len(block.split()) for block in story_blocks(text, 10000))


def block_word_count(block: str) -> int:
    return 0 if block == STORY_DIVIDER else len(block.split())


def page_word_count(blocks: list[str]) -> int:
    return sum(block_word_count(block) for block in blocks)


def has_story_dividers(text: str) -> bool:
    return STORY_DIVIDER in story_blocks(text, body_word_count(text), include_dividers=True)


def clean_page_edges(blocks: list[str]) -> list[str]:
    cleaned = list(blocks)
    while cleaned and cleaned[0] == STORY_DIVIDER:
        cleaned.pop(0)
    while cleaned and cleaned[-1] == STORY_DIVIDER:
        cleaned.pop()
    return cleaned


def is_section_heading_block(block: str) -> bool:
    return bool(re.fullmatch(r"\*\*[^*]+\*\*", block.strip()))


def rebalance_orphan_section_headings(pages: list[list[str]]) -> list[list[str]]:
    balanced = [list(page) for page in pages]
    for index in range(len(balanced) - 1):
        page = balanced[index]
        while page and page[-1] == STORY_DIVIDER:
            page.pop()
        if not page or not is_section_heading_block(page[-1]):
            continue
        heading = page.pop()
        if page and page[-1] == STORY_DIVIDER:
            page.pop()
        next_page = balanced[index + 1]
        while next_page and next_page[0] == STORY_DIVIDER:
            next_page.pop(0)
        next_page.insert(0, heading)
    return [clean_page_edges(page) for page in balanced if clean_page_edges(page)]


def rebalance_short_tail_pages(
    pages: list[list[str]],
    first_budget: int,
    continuation_budget: int,
    minimum_tail_words: int = 120,
) -> list[list[str]]:
    if len(pages) < 2:
        return pages

    balanced = [clean_page_edges(page) for page in pages if clean_page_edges(page)]
    if len(balanced) < 2:
        return balanced

    last_words = page_word_count(balanced[-1])
    if last_words >= minimum_tail_words:
        return rebalance_orphan_section_headings(balanced)

    merge_tolerance = max(75, int(continuation_budget * 0.12))
    if len(balanced) > 2 and page_word_count(balanced[-2]) + last_words <= continuation_budget + merge_tolerance:
        balanced[-2] = clean_page_edges(balanced[-2] + balanced[-1])
        balanced.pop()
        return rebalance_orphan_section_headings(balanced)

    previous_index = -2
    previous_floor = int((first_budget if len(balanced) == 2 else continuation_budget) * 0.62)
    while (
        page_word_count(balanced[-1]) < minimum_tail_words
        and len(balanced[previous_index]) > 1
        and page_word_count(balanced[previous_index]) > previous_floor
    ):
        moved = balanced[previous_index].pop()
        if moved == STORY_DIVIDER:
            continue
        balanced[-1].insert(0, moved)

    return rebalance_orphan_section_headings(balanced)


def css_token(value: str) -> str:
    token = re.sub(r"[^a-z0-9_-]+", "-", value.strip().lower())
    return "-".join(part for part in token.split("-") if part)


def story_layout_override(entry: book_build.BookEntry, design: dict[str, Any]) -> dict[str, Any]:
    overrides = (design.get("layout") or {}).get("layout_overrides") or {}
    for key in (entry.byte_index, entry.byte_index.lower(), entry.bit.slug, entry.bit.slug.lower()):
        value = overrides.get(key)
        if isinstance(value, dict):
            return value
        if value:
            return {"variant": value, "teaser": False}
    return {}


def layout_variant(entry: book_build.BookEntry, design: dict[str, Any]) -> str:
    override = story_layout_override(entry, design)
    value = override.get("variant")
    if value:
        token = css_token(str(value))
        return f"variant-{token}" if token else ""

    if has_story_dividers(entry.bit.body):
        return "variant-transcript-ledger"
    if entry.layout_mode == "glitch":
        return "variant-glitch-dossier"
    if entry.layout_mode == "signal" and body_word_count(entry.bit.body) >= 520:
        return "variant-signal-broadside"
    return ""


def teaser_enabled(entry: book_build.BookEntry, design: dict[str, Any]) -> bool:
    override = story_layout_override(entry, design)
    if "teaser" in override:
        return bool(override["teaser"])
    return not is_spread_entry(entry, design)


def story_style(entry: book_build.BookEntry, design: dict[str, Any]) -> str:
    override = story_layout_override(entry, design)
    style_keys = {
        "title_font": "--story-title-font",
        "title_size": "--story-title-size",
        "title_leading": "--story-title-leading",
        "body_font": "--story-body-font",
        "body_size": "--story-body-size",
        "body_leading": "--story-body-leading",
        "body_tracking": "--story-body-tracking",
        "body_width": "--story-body-width",
        "teaser_font": "--story-teaser-font",
        "teaser_size": "--story-teaser-size",
        "plate_height": "--story-plate-height",
    }
    declarations = []
    for key, css_var in style_keys.items():
        value = override.get(key)
        if value:
            declarations.append(f"{css_var}: {html.escape(str(value), quote=True)}")
    return "; ".join(declarations)


def section_css_class(code: str) -> str:
    token = css_token(str(code))
    return f"section-{token}" if token else "section-unknown"


def section_palette_css(design: dict[str, Any], palette_name: str) -> str:
    section_palettes = (design.get("section_palettes") or {}).get(palette_name) or {}
    rules: list[str] = []
    for code, colors in section_palettes.items():
        if not isinstance(colors, dict):
            continue
        declarations = []
        for key, css_var in (
            ("accent", "--section-accent"),
            ("accent_2", "--section-accent-2"),
            ("accent_3", "--section-accent-3"),
            ("wash", "--section-wash"),
        ):
            value = colors.get(key)
            if value:
                declarations.append(f"{css_var}: {html.escape(str(value), quote=True)}")
        if declarations:
            rules.append(f".{section_css_class(str(code))} {{{'; '.join(declarations)};}}")
    return "\n".join(rules)


def mode_css_class(mode: str) -> str:
    token = css_token(str(mode))
    return f"mode-{token}" if token else "mode-unknown"


def mode_palette_css(design: dict[str, Any], palette_name: str) -> str:
    mode_panels = design.get("mode_panels") or {}
    rules: list[str] = []
    for mode, data in mode_panels.items():
        if not isinstance(data, dict):
            continue
        colors = data.get(palette_name) or {}
        if not isinstance(colors, dict):
            continue
        declarations = []
        for key, css_var in (
            ("accent", "--mode-accent"),
            ("accent_2", "--mode-accent-2"),
            ("accent_3", "--mode-accent-3"),
            ("wash", "--mode-wash"),
        ):
            value = colors.get(key)
            if value:
                declarations.append(f"{css_var}: {html.escape(str(value), quote=True)}")
        if declarations:
            rules.append(f".{mode_css_class(str(mode))} {{{'; '.join(declarations)};}}")
    return "\n".join(rules)


def mode_panel_uri(design: dict[str, Any], mode: str, assets: AssetResolver | None = None) -> str:
    mode_panels = design.get("mode_panels") or {}
    data = mode_panels.get(mode) if isinstance(mode_panels, dict) else None
    if not isinstance(data, dict):
        return ""
    value = data.get("asset_path")
    if not value:
        return ""
    path = Path(str(value))
    return assets.uri(path) if assets else image_data_uri(path)


def cover_art_uri(design: dict[str, Any], assets: AssetResolver | None = None) -> str:
    cover = design.get("cover") or {}
    if not isinstance(cover, dict):
        return ""
    value = cover.get("asset_path")
    if not value:
        return ""
    path = Path(str(value))
    return assets.uri(path) if assets else image_data_uri(path)


def section_panel_uri(design: dict[str, Any], code: str, assets: AssetResolver | None = None) -> str:
    section_panels = design.get("section_panels") or {}
    if not isinstance(section_panels, dict):
        return ""
    data = section_panels.get(str(code).upper()) or section_panels.get(str(code).lower()) or section_panels.get(str(code))
    if not isinstance(data, dict):
        return ""
    value = data.get("asset_path")
    if not value:
        return ""
    path = Path(str(value))
    return assets.uri(path) if assets else image_data_uri(path)


def section_open_tag(entry: book_build.BookEntry, design: dict[str, Any], extra_classes: str = "") -> str:
    classes = entry_classes(entry, design)
    if extra_classes:
        classes = f"{classes} {extra_classes.strip()}"
    style = story_style(entry, design)
    style_attr = f' style="{style}"' if style else ""
    return f'<section class="{classes}" data-byte="{html.escape(entry.byte_index)}"{style_attr}>'


def entry_classes(entry: book_build.BookEntry, design: dict[str, Any]) -> str:
    classes = ["page", "entry", section_css_class(entry.section_code), entry.layout_mode]
    words = body_word_count(entry.bit.body)
    title_chars = len(entry.bit.title)
    if words <= 360:
        classes.append("entry-short")
        classes.append("copy-airy")
        if words <= 240:
            classes.append("copy-quote")
    elif words >= 600:
        classes.append("entry-long")
        classes.append("copy-dense")
    else:
        classes.append("entry-medium")
        classes.append("copy-balanced")
    if title_chars <= 15:
        classes.append("title-short")
    elif title_chars >= 28:
        classes.append("title-long")
    classes.append(art_layout_class(entry))
    if has_story_dividers(entry.bit.body):
        classes.append("sectioned-transcript")
    variant = layout_variant(entry, design)
    if variant:
        classes.append(variant)
    if not teaser_enabled(entry, design):
        classes.append("no-teaser")
    return " ".join(html.escape(class_name) for class_name in classes)


def art_layout_class(entry: book_build.BookEntry) -> str:
    words = body_word_count(entry.bit.body)
    if entry.layout_mode == "glitch":
        return "art-quad"
    if words <= 240 and entry.layout_mode in {"signal", "myth"}:
        return "art-side"
    if words <= 360 and entry.layout_mode in {"archive", "protocol"}:
        return "art-inset"
    if words <= 520 and entry.layout_mode in {"field_note", "signal"}:
        return "art-side"
    return "art-band"


def entry_excerpt_word_limit(entry: book_build.BookEntry, design: dict[str, Any]) -> int:
    base_words = int(design.get("layout", {}).get("entry_excerpt_words", 340))
    single_page_full_words = int(design.get("layout", {}).get("single_page_full_words", 360))
    words = body_word_count(entry.bit.body)
    if words <= single_page_full_words:
        return words
    dense_limits = {
        "signal": 350,
        "glitch": 430,
        "protocol": 455,
        "field_note": 500,
        "archive": 500,
        "myth": 470,
    }
    balanced_limits = {
        "signal": 340,
        "glitch": 340,
        "protocol": 390,
        "field_note": 400,
        "archive": 400,
        "myth": 380,
    }
    limits = dense_limits if words >= 600 else balanced_limits
    return min(words, limits.get(entry.layout_mode, base_words))


def is_excerpted(entry: book_build.BookEntry, design: dict[str, Any]) -> bool:
    return body_word_count(entry.bit.body) > entry_excerpt_word_limit(entry, design)


def is_spread_entry(entry: book_build.BookEntry, design: dict[str, Any]) -> bool:
    override = story_layout_override(entry, design)
    if override.get("force_single_page"):
        return False
    if override.get("force_spread"):
        return True
    forced = design.get("layout", {}).get("two_page_spread_entries") or []
    forced_refs = {str(ref).lower() for ref in forced}
    if entry.byte_index.lower() in forced_refs or entry.bit.slug.lower() in forced_refs:
        return True
    if has_story_dividers(entry.bit.body):
        sectioned_limit = int(design.get("layout", {}).get("sectioned_single_page_full_words", 340))
        if body_word_count(entry.bit.body) > sectioned_limit:
            return True
    mode_limits = design.get("layout", {}).get("single_page_full_words_by_mode") or {}
    single_page_full_words = int(mode_limits.get(entry.layout_mode, design.get("layout", {}).get("single_page_full_words", 360)))
    if body_word_count(entry.bit.body) > single_page_full_words:
        return True
    threshold = int(design.get("layout", {}).get("two_page_spread_min_words", 700))
    return body_word_count(entry.bit.body) >= threshold


def spread_excerpt_word_limit(entry: book_build.BookEntry, design: dict[str, Any]) -> int:
    return body_word_count(entry.bit.body)


def story_page_budgets(entry: book_build.BookEntry, design: dict[str, Any]) -> tuple[int, int]:
    layout = design.get("layout", {})
    first = int(layout.get("spread_first_page_words", 230))
    continuation = int(layout.get("spread_continuation_words", 560))
    if has_story_dividers(entry.bit.body):
        first = int(layout.get("sectioned_first_page_words", 170))
        continuation = int(layout.get("sectioned_continuation_words", 360))
    override = story_layout_override(entry, design)
    if override.get("first_page_words"):
        first = int(override["first_page_words"])
    if override.get("continuation_words"):
        continuation = int(override["continuation_words"])
    return first, continuation


def split_story_for_pages(entry: book_build.BookEntry, design: dict[str, Any]) -> list[list[str]]:
    first_budget, continuation_budget = story_page_budgets(entry, design)
    blocks = story_blocks(entry.bit.body, body_word_count(entry.bit.body), include_dividers=True)
    pages: list[list[str]] = []
    current: list[str] = []
    current_words = 0

    for block in blocks:
        words = block_word_count(block)
        budget = first_budget if not pages else continuation_budget
        if block == STORY_DIVIDER:
            if current and current[-1] != STORY_DIVIDER:
                current.append(block)
            continue
        if current and current_words + words > budget:
            while current and current[-1] == STORY_DIVIDER:
                current.pop()
            pages.append(current)
            current = []
            current_words = 0
            budget = continuation_budget
        if words > budget:
            tokens = block.split()
            start = 0
            while start < len(tokens):
                if current:
                    pages.append(current)
                    current = []
                    current_words = 0
                chunk_size = continuation_budget if pages else budget
                current.append(" ".join(tokens[start : start + chunk_size]))
                current_words = min(chunk_size, len(tokens) - start)
                start += chunk_size
            continue
        current.append(block)
        current_words += words

    while current and current[-1] == STORY_DIVIDER:
        current.pop()
    if current:
        pages.append(current)
    return rebalance_short_tail_pages(pages, first_budget, continuation_budget)


def split_story_for_spread(
    text: str,
    words: int,
    first_page_ratio: float = 0.34,
) -> tuple[list[str], list[str]]:
    blocks = story_blocks(text, words)
    total_words = sum(len(block.split()) for block in blocks)
    target = min(280, max(170, int(total_words * first_page_ratio)))
    first: list[str] = []
    second: list[str] = []
    count = 0
    in_second = False

    for block in blocks:
        block_words = len(block.split())
        if in_second:
            second.append(block)
            continue
        if first and count + block_words > target:
            second.append(block)
            in_second = True
            continue
        if not first and block_words > target:
            tokens = block.split()
            first.append(" ".join(tokens[:target]).rstrip() + "...")
            second.append(" ".join(tokens[target:]))
            count = target
            in_second = True
            continue
        first.append(block)
        count += block_words

    if not second and len(first) > 1:
        second = [first.pop()]

    return first, second


def pull_quote(text: str, limit: int = 120) -> str:
    abbreviations = ("Dr.", "Mr.", "Mrs.", "Ms.", "Prof.", "Sr.", "Jr.", "St.", "vs.", "etc.")
    for block in story_blocks(text, 120):
        stripped = re.sub(r"^#+\s*", "", block).strip()
        if not stripped:
            continue
        protected = stripped
        placeholders: dict[str, str] = {}
        for index, abbreviation in enumerate(abbreviations):
            placeholder = f"__ABBR_{index}__"
            placeholders[placeholder] = abbreviation
            protected = protected.replace(abbreviation, placeholder)
        sentence = re.split(r"(?<=[.!?])\s+", protected, maxsplit=1)[0]
        for placeholder, abbreviation in placeholders.items():
            sentence = sentence.replace(placeholder, abbreviation)
        if len(sentence) <= limit:
            return sentence
        return sentence[: limit - 3].rstrip() + "..."
    return ""


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


def count_label(count: int, singular: str = "entry", plural: str = "entries") -> str:
    word = singular if count == 1 else plural
    return f"{count:02d} {word}"


def mode_info(design: dict[str, Any], mode: str) -> dict[str, str]:
    modes = design.get("layout_modes") or {}
    data = modes.get(mode) or {}
    return {
        "label": str(data.get("label", mode.replace("_", " ").title())),
        "glyph": str(data.get("glyph", mode[:3].upper())),
        "description": str(data.get("description", "")),
    }


def art_direction_for(entry: book_build.BookEntry, art_direction: dict[str, Any]) -> dict[str, Any]:
    stories = art_direction.get("stories") or {}
    for key in (entry.byte_index, entry.byte_index.lower(), entry.bit.slug, entry.bit.slug.lower()):
        data = stories.get(key)
        if isinstance(data, dict):
            return data
    return {}


def mode_art_direction(mode: str, art_direction: dict[str, Any]) -> dict[str, Any]:
    data = (art_direction.get("mode_defaults") or {}).get(mode) or {}
    return data if isinstance(data, dict) else {}


def art_treatment_for(entry: book_build.BookEntry, art_direction: dict[str, Any]) -> str:
    story = art_direction_for(entry, art_direction)
    mode_default = mode_art_direction(entry.layout_mode, art_direction)
    treatment = str(story.get("treatment") or mode_default.get("treatment") or entry.layout_mode)
    token = css_token(treatment)
    return token or entry.layout_mode


def art_manifest_entry_for(entry: book_build.BookEntry, art_direction: dict[str, Any]) -> dict[str, Any]:
    entries = art_direction.get("_art_entries") or {}
    if not isinstance(entries, dict):
        return {}
    for key in (entry.bit.slug, entry.byte_index, entry.bit.title):
        data = entries.get(key)
        if isinstance(data, dict):
            return data
    return {}


def art_variant_entry_for(
    entry: book_build.BookEntry,
    art_direction: dict[str, Any],
    role: str = "opener",
    index: int = 0,
    allow_fallback: bool = True,
) -> dict[str, Any]:
    manifest_entry = art_manifest_entry_for(entry, art_direction)
    if role == "opener" or not manifest_entry:
        return manifest_entry
    continuation_assets = manifest_entry.get("continuation_assets")
    if not isinstance(continuation_assets, list):
        return manifest_entry if allow_fallback else {}
    candidates: list[dict[str, Any]] = []
    for item in continuation_assets:
        if not isinstance(item, dict):
            continue
        item_use = str(item.get("use") or "continuation")
        if item_use == role or (role == "tail" and item_use == "continuation") or (role == "continuation" and item_use == "middle"):
            page_index = item.get("page_index")
            if page_index is not None:
                try:
                    if int(page_index) != index:
                        continue
                except (TypeError, ValueError):
                    continue
            candidates.append(item)
    if not candidates:
        return manifest_entry if allow_fallback else {}
    selected = candidates[min(index, len(candidates) - 1)]
    merged = dict(manifest_entry)
    merged.update(selected)
    return merged


def art_asset_uri_for(
    entry: book_build.BookEntry,
    art_direction: dict[str, Any],
    role: str = "opener",
    index: int = 0,
    allow_fallback: bool = True,
    assets: AssetResolver | None = None,
) -> str:
    manifest_entry = art_variant_entry_for(entry, art_direction, role, index, allow_fallback)
    for key in ("asset_path", "draft_asset_path", "approved_asset_path"):
        value = manifest_entry.get(key)
        if not value:
            continue
        path = Path(str(value))
        uri = assets.uri(path) if assets else image_data_uri(path)
        if uri:
            return uri
    return ""


def art_style_for(
    entry: book_build.BookEntry,
    art_direction: dict[str, Any],
    role: str = "opener",
    index: int = 0,
    allow_fallback: bool = True,
) -> str:
    manifest_entry = art_variant_entry_for(entry, art_direction, role, index, allow_fallback)
    declarations: list[str] = []
    allowed = {
        "fit": "--art-fit",
        "position": "--art-position",
        "opacity": "--art-opacity",
        "saturate": "--art-saturate",
        "contrast": "--art-contrast",
        "brightness": "--art-brightness",
        "scale": "--art-scale",
    }
    for key, css_var in allowed.items():
        value = manifest_entry.get(key)
        if value:
            declarations.append(f"{css_var}: {html.escape(str(value), quote=True)}")
    return "; ".join(declarations)


def art_classes_for(
    entry: book_build.BookEntry,
    art_direction: dict[str, Any],
    role: str = "opener",
    index: int = 0,
    allow_fallback: bool = True,
) -> str:
    manifest_entry = art_variant_entry_for(entry, art_direction, role, index, allow_fallback)
    classes = []
    layout = css_token(str(manifest_entry.get("art_layout") or ""))
    if layout:
        classes.append(f"art-layout-{layout}")
    if role != "opener":
        classes.append(f"art-role-{css_token(role) or 'continuation'}")
    if manifest_entry.get("edge_fade"):
        classes.append("edge-fade")
    if manifest_entry.get("soft_matte"):
        classes.append("soft-matte")
    if manifest_entry.get("quiet_labels"):
        classes.append("quiet-labels")
    return " ".join(classes)


def has_explicit_art_variant_for(
    entry: book_build.BookEntry,
    art_direction: dict[str, Any],
    role: str,
    index: int,
) -> bool:
    manifest_entry = art_variant_entry_for(entry, art_direction, role, index, allow_fallback=False)
    return any(manifest_entry.get(key) for key in ("asset_path", "draft_asset_path", "approved_asset_path"))


def variant_token(entry: book_build.BookEntry, design: dict[str, Any]) -> str:
    variant = layout_variant(entry, design)
    return variant.removeprefix("variant-") if variant else ""


def plate_identity(entry: book_build.BookEntry, design: dict[str, Any]) -> dict[str, str]:
    mode = mode_info(design, entry.layout_mode)
    override = story_layout_override(entry, design)
    identities = {
        "static-tower": {"glyph": "RX", "label": "Water Tower Signal"},
        "frostbite-core": {"glyph": "ICE", "label": "Cryo Core"},
        "router-love-letter": {"glyph": "RTR", "label": "Router Letter"},
        "genome-foldout": {"glyph": "DNA", "label": "Genome Foldout"},
        "listening-chair": {"glyph": "CHR", "label": "Listening Chair"},
        "receipt-transcript": {"glyph": "RCT", "label": "Receipt Transcript"},
        "pneumatic-tube": {"glyph": "PNE", "label": "Pneumatic Tube"},
        "detergent-ticket": {"glyph": "DET", "label": "Detergent Ticket"},
        "vhs-echo": {"glyph": "VHS", "label": "VHS Echo"},
        "infinite-windows": {"glyph": "WIN", "label": "Infinite Windows"},
        "ghost-ship": {"glyph": "DRF", "label": "Ghost Ship Drift"},
        "static-echoes": {"glyph": "EKO", "label": "Static Echo"},
        "midway-mind": {"glyph": "MID", "label": "Midway Mind"},
        "memory-weaver": {"glyph": "MEM", "label": "Memory Weave"},
        "dialup-entanglement": {"glyph": "56K", "label": "Dial-Up Entanglement"},
        "silent-ward": {"glyph": "WRD", "label": "Silent Ward"},
        "underworld-ledger": {"glyph": "LED", "label": "Underworld Ledger"},
    }
    identity = identities.get(variant_token(entry, design))
    if identity:
        identity = dict(identity)
    else:
        identity = {"glyph": mode["glyph"], "label": mode["label"]}
    if override.get("art_glyph"):
        identity["glyph"] = str(override["art_glyph"])
    art_titles = override.get("art_titles")
    if isinstance(art_titles, list) and art_titles:
        identity["label"] = " / ".join(str(title) for title in art_titles if title)
    elif override.get("art_title"):
        identity["label"] = str(override["art_title"])
    elif override.get("art_label"):
        identity["label"] = str(override["art_label"])
    return identity


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


def contents_register(entries: list[book_build.BookEntry]) -> str:
    filled = len(entries)
    first = entries[0].byte_index if entries else "00"
    last = entries[-1].byte_index if entries else "FF"
    return (
        '<div class="toc-register">'
        f"<span>{filled:03d} / 256 occupied</span>"
        f"<span>{html.escape(first)} to {html.escape(last)} observed</span>"
        f"<span>{256 - filled:03d} dark addresses remain</span>"
        "</div>"
    )


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
    mark_filter = "filter: invert(1);" if palette_name == "dark" else ""
    cover_art_filter = (
        "filter: invert(0.92) hue-rotate(165deg) saturate(0.92) brightness(0.76) contrast(1.08);"
        if palette_name == "dark"
        else ""
    )
    cover_art_blend = "screen" if palette_name == "dark" else "multiply"
    cover_art_opacity = "0.58" if palette_name == "dark" else "0.88"
    spectrum_blend = "screen" if palette_name == "dark" else "multiply"
    mode_panel_blend = "screen" if palette_name == "dark" else "multiply"
    section_css = section_palette_css(design, palette_name)
    mode_css = mode_palette_css(design, palette_name)
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
  --paper: {palette["paper"]};
  --section-accent: {palette["accent"]};
  --section-accent-2: {palette["accent_2"]};
  --section-accent-3: {palette["accent_3"]};
  --section-wash: {palette["paper_alt"]};
  --mode-accent: {palette["accent"]};
  --mode-accent-2: {palette["accent_2"]};
  --mode-accent-3: {palette["accent_3"]};
  --mode-wash: {palette["paper_alt"]};
  width: {trim["width"]};
  height: {trim["height"]};
  break-after: page;
  page-break-after: always;
  position: relative;
  overflow: hidden;
  padding: {trim["margin_top"]} {trim["margin_outer"]} {trim["margin_bottom"]} {trim["margin_inner"]};
  background: {palette["paper"]};
}}
{section_css}
{mode_css}
.page:nth-child(even) {{
  padding-left: {trim["margin_outer"]};
  padding-right: {trim["margin_inner"]};
}}
.cover {{
  display: grid;
  grid-template-rows: auto 1fr auto;
  overflow: hidden;
  background:
    linear-gradient(90deg, transparent 0 63%, color-mix(in srgb, {palette["accent_2"]} 30%, transparent) 64% 79%, transparent 80%),
    linear-gradient(135deg, color-mix(in srgb, {palette["accent"]} 18%, transparent), transparent 42%),
    linear-gradient(180deg, {palette["paper"]}, {palette["paper_alt"]});
}}
.cover::before {{
  content: "";
  position: absolute;
  left: 0.64in;
  right: 0.64in;
  bottom: 0.62in;
  height: 3.18in;
  border: 1px solid color-mix(in srgb, {palette["rule"]} 66%, transparent);
  background:
    repeating-linear-gradient(90deg, transparent 0 0.19in, color-mix(in srgb, {palette["accent_2"]} 18%, transparent) 0.2in 0.21in),
    repeating-linear-gradient(180deg, transparent 0 0.29in, color-mix(in srgb, {palette["accent"]} 12%, transparent) 0.3in 0.31in),
    linear-gradient(135deg, color-mix(in srgb, {palette["paper_alt"]} 86%, transparent), transparent);
  opacity: 0.18;
  z-index: 1;
}}
.cover > .cover-art {{
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: cover;
  object-position: center center;
  opacity: {cover_art_opacity};
  mix-blend-mode: {cover_art_blend};
  {cover_art_filter}
  z-index: 0;
}}
.cover > * {{
  position: relative;
  z-index: 4;
}}
.cover-top {{
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 0.35in;
}}
.cover-code {{
  font-family: {typo["mono"]};
  font-size: 9px;
  line-height: 1.65;
  color: {palette["muted"]};
  max-width: 2.2in;
  letter-spacing: 0.08em;
}}
.brand-mark {{
  width: 0.62in;
  height: 0.62in;
  object-fit: contain;
  opacity: 0.78;
  {mark_filter}
}}
.cover .brand-mark {{
  width: 0.72in;
  height: 0.72in;
  margin-top: -0.03in;
}}
.cover h1 {{
  font-family: {typo["display"]};
  font-size: 72px;
  line-height: 0.88;
  margin: 0 0 0.18in;
  font-weight: 400;
  text-shadow: 0 1px 0 color-mix(in srgb, {palette["paper"]} 52%, transparent);
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
.endpaper {{
  display: grid;
  grid-template-rows: auto minmax(0, 1fr) auto;
  background:
    radial-gradient(circle at 78% 18%, color-mix(in srgb, {palette["accent_2"]} 22%, transparent), transparent 22%),
    linear-gradient(135deg, {palette["paper_alt"]}, {palette["paper"]});
}}
.endpaper-label {{
  font-family: {typo["mono"]};
  color: {palette["accent"]};
  font-size: 8px;
  letter-spacing: 0.18em;
  text-transform: uppercase;
}}
.byte-field {{
  display: grid;
  grid-template-columns: repeat(16, minmax(0, 1fr));
  grid-template-rows: repeat(16, minmax(0, 1fr));
  gap: 0.025in;
  align-self: center;
  opacity: 0.82;
}}
.byte-field span {{
  display: grid;
  place-items: center;
  border: 1px solid color-mix(in srgb, {palette["rule"]} 52%, transparent);
  font-family: {typo["mono"]};
  font-size: 5.4px;
  color: color-mix(in srgb, {palette["muted"]} 72%, transparent);
  background: color-mix(in srgb, {palette["paper_alt"]} 58%, transparent);
}}
.byte-field span.filled {{
  color: {palette["ink"]};
  border-color: color-mix(in srgb, {palette["accent"]} 66%, transparent);
  background:
    linear-gradient(135deg, color-mix(in srgb, {palette["accent"]} 14%, transparent), transparent),
    color-mix(in srgb, {palette["paper_alt"]} 82%, transparent);
}}
.endpaper-back .byte-field {{
  transform: rotate(180deg);
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
    radial-gradient(circle at 78% 18%, color-mix(in srgb, var(--section-accent-2) 18%, transparent), transparent 24%),
    linear-gradient(120deg, color-mix(in srgb, var(--section-wash) 62%, {palette["paper_alt"]}), {palette["paper"]} 62%),
    repeating-linear-gradient(90deg, transparent 0 9px, color-mix(in srgb, var(--section-accent) 24%, transparent) 10px 11px);
}}
.section-mark {{
  font-family: {typo["mono"]};
  color: var(--section-accent);
  font-size: 82px;
  line-height: 0.8;
}}
.section-glyph {{
  font-family: {typo["mono"]};
  color: var(--section-accent-2);
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
  color: var(--section-accent);
}}
.section-tags span:nth-child(3n + 2) {{
  color: var(--section-accent-2);
}}
.section-tags span:nth-child(3n) {{
  color: var(--section-accent-3);
}}
.section-panel {{
  position: relative;
  height: 2.18in;
  margin-top: 0.34in;
  border-top: 1px solid color-mix(in srgb, var(--section-accent) 60%, transparent);
  border-bottom: 1px solid color-mix(in srgb, var(--section-accent-2) 42%, transparent);
  overflow: hidden;
  background:
    linear-gradient(135deg, color-mix(in srgb, var(--section-wash) 52%, transparent), transparent),
    {palette["paper_alt"]};
}}
.section-panel img {{
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
  filter: saturate(0.78) contrast(1.02) brightness(1.03);
}}
.section-panel::after {{
  content: "";
  position: absolute;
  inset: 0;
  pointer-events: none;
  background:
    linear-gradient(90deg, {palette["paper"]} 0%, transparent 9%, transparent 91%, {palette["paper"]} 100%),
    linear-gradient(180deg, color-mix(in srgb, {palette["paper"]} 30%, transparent), transparent 28%, transparent 72%, color-mix(in srgb, {palette["paper"]} 36%, transparent));
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
  border-color: var(--section-accent);
}}
.entry {{
  display: grid;
  grid-template-areas:
    "head"
    "plate"
    "quote"
    "body"
    "foot";
  grid-template-rows: auto minmax(1.38in, 1.72in) auto minmax(0, 1fr) auto;
  gap: 0.13in;
  min-width: 0;
}}
.entry > * {{
  position: relative;
  z-index: 1;
}}
.entry-head {{
  grid-area: head;
  display: grid;
  grid-template-columns: 0.72in 1fr;
  gap: 0.18in;
  align-items: end;
  min-width: 0;
}}
.entry-head > div {{
  min-width: 0;
}}
.byte {{
  font-family: {typo["mono"]};
  color: var(--section-accent);
  font-size: 28px;
  line-height: 1;
}}
.entry h3 {{
  font-family: var(--story-title-font, {typo["display"]});
  font-size: var(--story-title-size, 30px);
  line-height: var(--story-title-leading, 1.02);
  margin: 0;
  font-weight: 400;
}}
.entry.title-short h3 {{
  font-size: var(--story-title-size, 34px);
  line-height: 0.98;
}}
.entry.title-long h3 {{
  font-size: var(--story-title-size, 26px);
  line-height: 1.04;
}}
.meta {{
  margin-top: 0.08in;
  font-family: {typo["sans"]};
  color: {palette["muted"]};
  text-transform: uppercase;
  letter-spacing: 0.08em;
  font-size: 8px;
  max-width: 100%;
  overflow-wrap: anywhere;
}}
.gen-ref {{
  color: var(--section-accent-2);
  font-family: {typo["mono"]};
  white-space: normal;
}}
.mode-badge {{
  display: inline-block;
  margin-top: 0.07in;
  font-family: {typo["mono"]};
  font-size: 7px;
  color: var(--section-accent-2);
  letter-spacing: 0.16em;
  text-transform: uppercase;
}}
.entry-body {{
  grid-area: body;
  font-size: var(--story-body-size, 11.8px);
  line-height: var(--story-body-leading, 1.48);
  letter-spacing: var(--story-body-tracking, 0);
  font-family: var(--story-body-font, {typo["body"]});
  text-align: left;
  hyphens: auto;
  overflow-wrap: break-word;
  min-width: 0;
  min-height: 0;
  max-width: 100%;
  max-height: 100%;
  overflow: hidden;
  align-self: start;
}}
.entry-body p {{
  margin: 0 0 0.085in;
  max-width: 100%;
  overflow-wrap: break-word;
}}
.entry-body p:last-child {{
  margin-bottom: 0;
}}
.entry-body em {{
  font-style: italic;
}}
.entry-body strong {{
  font-weight: 600;
}}
.story-divider {{
  border: 0;
  border-top: 1px solid color-mix(in srgb, var(--section-accent) 72%, transparent);
  margin: 0.11in 0;
}}
.entry-short .entry-body {{
  max-width: 4.55in;
  margin: 0 auto;
  font-size: var(--story-body-size, 11.4px);
  line-height: var(--story-body-leading, 1.46);
}}
.entry-long .entry-body {{
  font-size: var(--story-body-size, 10.9px);
  line-height: var(--story-body-leading, 1.44);
}}
.entry-short {{
  grid-template-rows: auto minmax(1.72in, 2.12in) auto minmax(0, 1fr) auto;
}}
.entry-long {{
  grid-template-rows: auto minmax(1.0in, 1.22in) auto minmax(0, 1fr) auto;
  gap: 0.12in;
}}
.entry-long .plate {{
  min-height: 1.0in;
}}
.entry-short .plate {{
  min-height: 1.72in;
}}
.copy-airy .entry-body {{
  align-self: center;
}}
.copy-balanced .plate {{
  min-height: 1.45in;
}}
.copy-dense .entry-body {{
  font-size: var(--story-body-size, 10.2px);
  line-height: var(--story-body-leading, 1.42);
}}
.entry.signal .entry-body {{
  font-family: var(--story-body-font, {typo["mono"]});
  font-size: var(--story-body-size, 9px);
  line-height: var(--story-body-leading, 1.55);
  border-left: 2px solid var(--section-accent-2);
  padding-left: 0.18in;
}}
.entry.signal.copy-dense .entry-body {{
  font-size: 8.55px;
  line-height: 1.47;
}}
.variant-router-love-letter.entry.signal.copy-dense .entry-body {{
  font-size: var(--story-body-size, 8.05px);
  line-height: var(--story-body-leading, 1.34);
}}
.variant-router-love-letter.entry.signal.copy-dense .entry-body p {{
  margin-bottom: 0.045in;
}}
.variant-router-love-letter.entry.signal.copy-dense .entry-body p:last-child {{
  margin-bottom: 0;
}}
.entry.archive .entry-body {{
  font-size: var(--story-body-size, 10.4px);
  line-height: var(--story-body-leading, 1.44);
  border-top: 1px solid {palette["rule"]};
  border-bottom: 1px solid {palette["rule"]};
  padding: 0.12in 0;
}}
.entry.archive .entry-body p + p {{
  text-indent: 0.12in;
}}
.entry.field_note .entry-body {{
  font-size: var(--story-body-size, 9.25px);
  line-height: var(--story-body-leading, 1.43);
  background: repeating-linear-gradient(180deg, transparent 0 22px, color-mix(in srgb, {palette["rule"]} 48%, transparent) 23px 24px);
  padding: 0.08in 0.1in;
}}
.entry.field_note .entry-body p {{
  margin-bottom: 0.06in;
}}
.entry.protocol .entry-body {{
  font-family: var(--story-body-font, {typo["sans"]});
  font-size: var(--story-body-size, 10px);
  line-height: var(--story-body-leading, 1.5);
  border: 1px solid {palette["rule"]};
  padding: 0.16in;
}}
.entry.protocol.copy-airy .entry-body {{
  align-self: start;
  margin-top: 0.16in;
}}
.entry.protocol.copy-dense .entry-body {{
  font-size: 9.25px;
  line-height: 1.42;
  padding: 0.14in;
}}
.entry.myth .entry-body {{
  font-size: 14px;
  line-height: 1.55;
  max-width: 4.9in;
  margin: 0 auto;
}}
.entry.glitch .entry-body {{
  font-family: var(--story-body-font, {typo["mono"]});
  font-size: var(--story-body-size, 8.8px);
  line-height: var(--story-body-leading, 1.5);
  transform: skewY(-0.35deg);
  border: 1px solid color-mix(in srgb, {palette["accent"]} 36%, transparent);
  padding: 0.13in;
}}
.entry.glitch.copy-dense .entry-body {{
  font-size: 8.1px;
  line-height: 1.42;
}}
.entry.glitch.entry-medium .entry-body {{
  font-size: var(--story-body-size, 7.95px);
  line-height: var(--story-body-leading, 1.34);
  padding: 0.11in;
}}
.entry.glitch.entry-medium .entry-body p {{
  margin-bottom: 0.05in;
}}
.entry.glitch .entry-body p:nth-child(even) {{
  margin-left: 0.12in;
}}
.sectioned-transcript .entry-body {{
  font-family: var(--story-body-font, {typo["mono"]});
  font-size: var(--story-body-size, 7.7px);
  line-height: var(--story-body-leading, 1.26);
}}
.sectioned-transcript .entry-body p {{
  margin-bottom: 0.026in;
}}
.sectioned-transcript .entry-body strong {{
  color: {palette["accent_2"]};
  font-weight: 600;
}}
.sectioned-transcript .story-divider {{
  border-top: 1px solid {palette["accent"]};
  margin: 0.055in 0;
}}
.entry-pullquote {{
  grid-area: quote;
  display: none;
}}
.copy-airy.copy-quote .entry-pullquote {{
  display: block;
  max-width: 4.8in;
  margin: -0.02in auto 0;
  font-family: {typo["display"]};
  color: {palette["accent"]};
  font-size: 20px;
  line-height: 1.12;
  text-align: center;
}}
.plate {{
  grid-area: plate;
  border: 1px solid {palette["rule"]};
  background:
    radial-gradient(circle at 18% 24%, {palette["plate_c"]} 0 9%, transparent 10%),
    radial-gradient(circle at 82% 58%, {palette["plate_b"]} 0 13%, transparent 14%),
    linear-gradient(135deg, {palette["plate_a"]}, {palette["paper_alt"]});
  display: grid;
  place-items: center;
  position: relative;
  overflow: hidden;
  isolation: isolate;
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
  z-index: 1;
}}
.plate::after {{
  content: "";
  position: absolute;
  inset: 0;
  background:
    linear-gradient(90deg, transparent 0 49%, color-mix(in srgb, {palette["ink"]} 10%, transparent) 50%, transparent 51%),
    repeating-linear-gradient(0deg, transparent 0 17px, color-mix(in srgb, {palette["ink"]} 8%, transparent) 18px 19px);
  mix-blend-mode: multiply;
  opacity: 0.55;
  z-index: 0;
}}
.plate-label {{
  position: absolute;
  left: 0.22in;
  bottom: 0.18in;
  max-width: calc(100% - 0.44in);
  font-family: {typo["mono"]};
  color: color-mix(in srgb, {palette["ink"]} 62%, transparent);
  font-size: 7.2px;
  line-height: 1.3;
  letter-spacing: 0.16em;
  text-transform: uppercase;
  z-index: 2;
  text-align: left;
}}
.plate-notes {{
  position: absolute;
  left: 0.22in;
  top: 0.16in;
  max-width: calc(100% - 0.44in);
  font-family: {typo["mono"]};
  color: color-mix(in srgb, {palette["ink"]} 48%, transparent);
  font-size: 5.8px;
  line-height: 1.35;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  z-index: 2;
}}
.plate-sigil {{
  position: absolute;
  right: 0.2in;
  top: 0.1in;
  font-family: {typo["mono"]};
  color: color-mix(in srgb, {palette["ink"]} 20%, transparent);
  font-size: 44px;
  line-height: 1;
  letter-spacing: 0.04em;
  z-index: 1;
}}
.plate-art-img {{
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: var(--art-fit, cover);
  object-position: var(--art-position, center center);
  opacity: var(--art-opacity, 1);
  transform: scale(var(--art-scale, 1));
  z-index: 0;
  filter:
    saturate(var(--art-saturate, 0.92))
    contrast(var(--art-contrast, 1.04))
    brightness(var(--art-brightness, 1));
}}
.plate.has-art.edge-fade .plate-art-img {{
  -webkit-mask-image:
    linear-gradient(90deg, transparent 0, black 8%, black 92%, transparent 100%),
    linear-gradient(180deg, transparent 0, black 7%, black 91%, transparent 100%);
  -webkit-mask-composite: source-in;
  mask-image:
    linear-gradient(90deg, transparent 0, black 8%, black 92%, transparent 100%),
    linear-gradient(180deg, transparent 0, black 7%, black 91%, transparent 100%);
  mask-composite: intersect;
}}
.plate.has-art.soft-matte::before {{
  inset: 0.07in;
  border-color: color-mix(in srgb, {palette["paper"]} 48%, transparent);
  box-shadow:
    inset 0 0 0.22in color-mix(in srgb, {palette["paper"]} 35%, transparent),
    inset 0 0 0.05in color-mix(in srgb, {palette["ink"]} 18%, transparent);
  z-index: 2;
}}
.plate.has-art::after {{
  z-index: 1;
  opacity: 0.34;
  background:
    linear-gradient(180deg, color-mix(in srgb, {palette["paper"]} 18%, transparent), transparent 26%, color-mix(in srgb, {palette["ink"]} 20%, transparent)),
    repeating-linear-gradient(0deg, transparent 0 17px, color-mix(in srgb, {palette["ink"]} 8%, transparent) 18px 19px);
}}
.plate.has-art .plate-grid,
.plate.has-art .plate-thread,
.plate.has-art .plate-orbit,
.plate.has-art .plate-dots,
.plate.has-art .plate-notch {{
  display: none;
}}
.plate.has-art .plate-label,
.plate.has-art .plate-notes {{
  z-index: 3;
  max-width: calc(100% - 0.44in);
  padding: 0.045in 0.06in;
  background: color-mix(in srgb, {palette["paper"]} 72%, transparent);
  backdrop-filter: blur(1.5px);
}}
.plate.has-art .plate-sigil {{
  z-index: 2;
  color: color-mix(in srgb, {palette["paper"]} 52%, transparent);
  text-shadow: 0 1px 10px color-mix(in srgb, {palette["ink"]} 34%, transparent);
}}
.plate.has-art.quiet-labels .plate-sigil {{
  opacity: 0.38;
}}
.plate.has-art.quiet-labels .plate-label,
.plate.has-art.quiet-labels .plate-notes {{
  background: color-mix(in srgb, {palette["paper"]} 58%, transparent);
}}
.entry-short .plate-sigil {{
  font-size: 64px;
}}
.entry-long .plate-sigil {{
  font-size: 32px;
}}
.plate-grid {{
  display: none;
  position: absolute;
  inset: 0.18in;
  grid-template-columns: 1fr 1fr;
  grid-template-rows: 1fr 1fr;
  gap: 0.08in;
  z-index: 1;
}}
.plate-grid span {{
  border: 1px solid color-mix(in srgb, {palette["ink"]} 16%, transparent);
  background:
    radial-gradient(circle at 68% 32%, color-mix(in srgb, {palette["accent_2"]} 38%, transparent) 0 16%, transparent 17%),
    linear-gradient(135deg, color-mix(in srgb, {palette["plate_a"]} 70%, transparent), color-mix(in srgb, {palette["paper_alt"]} 70%, transparent));
}}
.plate-grid span:nth-child(2) {{
  background:
    linear-gradient(90deg, color-mix(in srgb, {palette["accent"]} 42%, transparent) 0 18%, transparent 19%),
    linear-gradient(135deg, color-mix(in srgb, {palette["plate_b"]} 68%, transparent), color-mix(in srgb, {palette["paper_alt"]} 70%, transparent));
}}
.plate-grid span:nth-child(3) {{
  background:
    repeating-linear-gradient(90deg, transparent 0 9px, color-mix(in srgb, {palette["ink"]} 10%, transparent) 10px 11px),
    linear-gradient(135deg, color-mix(in srgb, {palette["plate_c"]} 58%, transparent), color-mix(in srgb, {palette["paper_alt"]} 70%, transparent));
}}
.plate-grid span:nth-child(4) {{
  background:
    radial-gradient(circle at 45% 45%, color-mix(in srgb, {palette["accent_3"]} 42%, transparent) 0 24%, transparent 25%),
    linear-gradient(135deg, color-mix(in srgb, {palette["paper_alt"]} 80%, transparent), color-mix(in srgb, {palette["plate_b"]} 55%, transparent));
}}
.plate-thread {{
  position: absolute;
  left: 0.18in;
  right: 0.18in;
  top: 50%;
  height: 1px;
  background: color-mix(in srgb, {palette["accent"]} 34%, transparent);
  transform: rotate(-2deg);
  z-index: 1;
}}
.plate-orbit {{
  position: absolute;
  width: 1.14in;
  height: 1.14in;
  right: 0.48in;
  bottom: 0.36in;
  border: 1px solid color-mix(in srgb, {palette["accent_2"]} 40%, transparent);
  border-radius: 999px;
  z-index: 1;
}}
.plate-dots {{
  position: absolute;
  inset: 0.2in;
  z-index: 1;
  opacity: 0.38;
  background-image: radial-gradient(color-mix(in srgb, {palette["ink"]} 28%, transparent) 0 1px, transparent 1.3px);
  background-size: 0.18in 0.18in;
  mask-image: linear-gradient(90deg, transparent, #000 20%, #000 80%, transparent);
}}
.plate-notch {{
  position: absolute;
  z-index: 1;
  width: 0.36in;
  height: 0.36in;
  border: 1px solid color-mix(in srgb, {palette["accent"]} 32%, transparent);
  opacity: 0.56;
}}
.plate-notch-a {{
  left: 0.18in;
  top: 0.18in;
  border-right: 0;
  border-bottom: 0;
}}
.plate-notch-b {{
  right: 0.18in;
  bottom: 0.18in;
  border-left: 0;
  border-top: 0;
}}
.plate-specimen-study .plate-dots,
.plate-ritual-diagram .plate-dots {{
  opacity: 0.24;
  background-size: 0.14in 0.14in;
}}
.plate-document-scan .plate-dots,
.plate-ledger-fragment .plate-dots {{
  mask-image: linear-gradient(180deg, transparent, #000 24%, #000 72%, transparent);
}}
.plate-glitch-fragment .plate-dots {{
  background-size: 0.09in 0.16in;
  transform: skewY(-8deg);
  opacity: 0.44;
}}
.plate-document-scan .plate-thread,
.plate-ledger-fragment .plate-thread {{
  top: 24%;
  transform: rotate(0deg);
}}
.plate-frequency-map .plate-orbit,
.plate-signal-diagram .plate-orbit {{
  width: 1.56in;
  height: 0.72in;
  border-radius: 50%;
  transform: rotate(-9deg);
}}
.plate-specimen-study .plate-orbit,
.plate-ritual-diagram .plate-orbit {{
  left: 50%;
  right: auto;
  top: 50%;
  bottom: auto;
  transform: translate(-50%, -50%);
}}
.plate-glitch-fragment .plate-thread {{
  height: 0.08in;
  top: 36%;
  background: color-mix(in srgb, {palette["accent"]} 44%, transparent);
  transform: skewY(-7deg);
}}
.plate-object-study .plate-orbit {{
  border-radius: 0;
  transform: rotate(7deg);
}}
.entry-foot {{
  grid-area: foot;
  display: grid;
  grid-template-columns: 1fr 0.78in;
  gap: 0.18in;
  align-items: end;
  min-width: 0;
}}
.art-side {{
  grid-template-columns: minmax(1.55in, 2.05in) minmax(0, 1fr);
  grid-template-rows: auto minmax(0, 1fr) auto auto;
  grid-template-areas:
    "head head"
    "plate body"
    "quote body"
    "foot foot";
  column-gap: 0.22in;
  row-gap: 0.14in;
}}
.art-side .plate {{
  min-height: 4.25in;
  height: 100%;
}}
.art-side .entry-body {{
  max-width: none;
  margin: 0;
  align-self: start;
}}
.art-side.copy-airy.copy-quote .entry-pullquote {{
  align-self: end;
  max-width: none;
  font-size: 16px;
  text-align: left;
  margin: 0;
}}
.art-inset {{
  grid-template-columns: minmax(0, 1fr) 1.72in;
  grid-template-rows: auto minmax(1.05in, auto) minmax(0, 1fr) auto;
  grid-template-areas:
    "head head"
    "body plate"
    "body quote"
    "foot foot";
  column-gap: 0.2in;
  row-gap: 0.14in;
}}
.art-inset .plate {{
  min-height: 2.55in;
  height: 2.55in;
}}
.art-inset .entry-body {{
  max-width: none;
  margin: 0;
  align-self: start;
}}
.art-inset.protocol.copy-airy .entry-body {{
  margin-top: 0;
}}
.variant-basement-lattice.art-inset {{
  grid-template-columns: minmax(0, 1fr) 2.35in;
  column-gap: 0.27in;
}}
.variant-basement-lattice.art-inset .plate {{
  align-self: start;
  height: 3.42in;
  min-height: 3.42in;
}}
.variant-basement-lattice.art-inset .entry-body {{
  border: 1px solid {palette["rule"]};
  padding: 0.15in;
}}
.variant-stage-terminal.art-inset {{
  grid-template-columns: minmax(0, 1fr) 2.42in;
  column-gap: 0.27in;
}}
.variant-stage-terminal.art-inset .plate {{
  align-self: start;
  height: 3.92in;
  min-height: 3.92in;
}}
.variant-stage-terminal.art-inset .entry-body {{
  border: 1px solid {palette["rule"]};
  padding: 0.15in;
}}
.art-quad {{
  grid-template-rows: auto minmax(1.35in, 1.72in) auto minmax(0, 1fr) auto;
}}
.art-quad .plate {{
  min-height: 1.42in;
}}
.entry.glitch.entry-medium.art-quad {{
  grid-template-rows: auto minmax(1.02in, 1.24in) auto minmax(0, 1fr) auto;
}}
.entry.glitch.entry-medium.art-quad .plate {{
  min-height: 1.05in;
}}
.art-quad .plate-grid {{
  display: grid;
}}
.art-band.copy-dense .plate {{
  min-height: 0.92in;
}}
.spread-open {{
  grid-template-columns: 1.62in minmax(0, 1fr);
  grid-template-rows: auto minmax(2.02in, 2.48in) minmax(0, 1fr) auto;
  grid-template-areas:
    "head head"
    "plate plate"
    "quote body"
    "foot foot";
  column-gap: 0.24in;
  row-gap: 0.14in;
}}
.spread-open .plate {{
  min-height: var(--story-plate-height, 2.24in);
}}
.spread-open .plate-sigil {{
  font-size: 58px;
}}
.spread-open .entry-pullquote {{
  display: block;
  align-self: start;
  max-width: none;
  margin: 0;
  padding-top: 0.06in;
  border-top: 2px solid {palette["accent"]};
  font-family: var(--story-teaser-font, {typo["display"]});
  color: {palette["accent"]};
  font-size: var(--story-teaser-size, 18px);
  line-height: 1.14;
  text-align: left;
}}
.spread-open .entry-body {{
  max-width: none;
  margin: 0;
  align-self: center;
}}
.spread-open.no-teaser {{
  grid-template-rows: auto minmax(1.48in, 1.84in) minmax(0, 1fr) auto;
  grid-template-columns: 1fr;
  grid-template-areas:
    "head"
    "plate"
    "body"
    "foot";
}}
.spread-open.no-teaser:has(.plate.has-art) {{
  grid-template-columns: minmax(2.18in, 2.58in) minmax(0, 1fr);
  grid-template-rows: auto minmax(0, 1fr) auto;
  grid-template-areas:
    "head head"
    "plate body"
    "foot foot";
  column-gap: 0.24in;
  row-gap: 0.16in;
}}
.spread-open.no-teaser:has(.plate.has-art) .plate {{
  align-self: stretch;
  min-height: 5.35in;
  height: 100%;
}}
.spread-open.no-teaser:has(.plate.has-art) .entry-body {{
  align-self: center;
  max-width: none;
  margin: 0;
}}
.spread-open.no-teaser:has(.plate.has-art.art-layout-foldout) {{
  grid-template-columns: minmax(2.82in, 3.18in) minmax(0, 1fr);
  column-gap: 0.22in;
}}
.spread-open.no-teaser:has(.plate.has-art.art-layout-foldout) .plate {{
  min-height: 5.5in;
}}
.spread-open.no-teaser:has(.plate.has-art.art-layout-packet) {{
  grid-template-columns: minmax(2.06in, 2.36in) minmax(0, 1fr);
  column-gap: 0.24in;
}}
.spread-open.no-teaser:has(.plate.has-art.art-layout-packet) .plate {{
  min-height: 5.42in;
}}
.spread-open.no-teaser:has(.plate.has-art.art-layout-municipal) {{
  grid-template-columns: minmax(2.48in, 2.82in) minmax(0, 1fr);
  column-gap: 0.24in;
}}
.spread-open.no-teaser:has(.plate.has-art.art-layout-municipal) .plate {{
  min-height: 5.48in;
}}
.spread-open.no-teaser:has(.plate.has-art.art-layout-dossier) {{
  grid-template-columns: minmax(2.54in, 2.9in) minmax(0, 1fr);
  column-gap: 0.22in;
}}
.spread-open.no-teaser:has(.plate.has-art.art-layout-dossier) .plate {{
  min-height: 5.52in;
}}
.spread-open.no-teaser:has(.plate.has-art.art-layout-echo) {{
  grid-template-columns: minmax(2.7in, 3.04in) minmax(0, 1fr);
  column-gap: 0.22in;
}}
.spread-open.no-teaser:has(.plate.has-art.art-layout-echo) .plate {{
  min-height: 5.5in;
}}
.spread-open.no-teaser::after {{
  content: attr(data-byte);
  position: absolute;
  right: 0.48in;
  bottom: 0.96in;
  width: 2.05in;
  height: 1.15in;
  display: grid;
  place-items: center;
  border-top: 1px solid color-mix(in srgb, {palette["rule"]} 64%, transparent);
  border-bottom: 1px solid color-mix(in srgb, {palette["rule"]} 64%, transparent);
  font-family: {typo["mono"]};
  font-size: 38px;
  letter-spacing: 0.08em;
  color: color-mix(in srgb, {palette["accent_2"]} 16%, transparent);
  background:
    repeating-linear-gradient(90deg, transparent 0 0.16in, color-mix(in srgb, {palette["rule"]} 34%, transparent) 0.17in 0.18in),
    color-mix(in srgb, {palette["paper_alt"]} 28%, transparent);
  z-index: 0;
}}
.spread-open.no-teaser::before {{
  content: "";
  position: absolute;
  left: 0.42in;
  bottom: 1.02in;
  width: 0.9in;
  height: 0.9in;
  border: 1px solid color-mix(in srgb, {palette["accent"]} 22%, transparent);
  transform: rotate(-3deg);
  background:
    linear-gradient(90deg, transparent 0 48%, color-mix(in srgb, {palette["accent"]} 18%, transparent) 49% 51%, transparent 52%),
    linear-gradient(0deg, transparent 0 48%, color-mix(in srgb, {palette["accent_2"]} 18%, transparent) 49% 51%, transparent 52%);
  z-index: 0;
}}
.spread-open.no-teaser .entry-body {{
  max-width: var(--story-body-width, 5.28in);
  margin: 0 auto;
  align-self: start;
}}
.spread-open.no-teaser.protocol .entry-body {{
  max-width: var(--story-body-width, 5.16in);
}}
.no-teaser .entry-pullquote {{
  display: none;
}}
.spread-open.protocol .entry-body {{
  border-left: 2px solid {palette["rule"]};
  border-top: 0;
  border-right: 0;
  border-bottom: 0;
}}
.variant-transcript-ledger.spread-open {{
  grid-template-rows: auto minmax(1.72in, 2.08in) minmax(0, 1fr) auto;
}}
.variant-transcript-ledger.spread-open .entry-body {{
  align-self: start;
}}
.variant-transcript-ledger.spread-open .entry-pullquote {{
  font-size: 17px;
  line-height: 1.1;
}}
.variant-signal-broadside.spread-open {{
  grid-template-columns: 1.42in minmax(0, 1fr);
}}
.variant-signal-broadside.spread-open .plate {{
  min-height: 2.34in;
}}
.variant-signal-broadside.spread-open .entry-pullquote {{
  font-size: 17px;
}}
.variant-archive-slab.spread-text .entry-body,
.variant-transcript-ledger.spread-text .entry-body {{
  max-width: 5.56in;
}}
.variant-glitch-dossier.spread-text .entry-body {{
  max-width: 5.46in;
}}
.variant-static-tower.spread-open {{
  grid-template-columns: 1.34in minmax(0, 1fr);
}}
.variant-static-tower .plate {{
  background:
    linear-gradient(90deg, transparent 0 18%, color-mix(in srgb, {palette["accent_2"]} 54%, transparent) 18% 18.5%, transparent 19%),
    repeating-radial-gradient(circle at 21% 48%, transparent 0 20px, color-mix(in srgb, {palette["accent_2"]} 24%, transparent) 21px 22px),
    repeating-linear-gradient(90deg, color-mix(in srgb, {palette["accent_2"]} 32%, transparent) 0 1px, transparent 2px 13px),
    linear-gradient(135deg, {palette["paper_alt"]}, {palette["plate_b"]});
}}
.variant-static-tower.spread-open .entry-pullquote {{
  border-top-color: {palette["accent_2"]};
  color: {palette["accent_2"]};
}}
.variant-frostbite-core .plate {{
  background:
    radial-gradient(circle at 50% 56%, color-mix(in srgb, {palette["accent_2"]} 58%, transparent) 0 15%, transparent 16%),
    radial-gradient(circle at 50% 56%, transparent 0 28%, color-mix(in srgb, {palette["accent_2"]} 36%, transparent) 29% 30%, transparent 31%),
    linear-gradient(90deg, color-mix(in srgb, {palette["ink"]} 16%, transparent) 0 1px, transparent 2px 28%, color-mix(in srgb, {palette["ink"]} 12%, transparent) 29% 30%, transparent 31%),
    repeating-linear-gradient(180deg, color-mix(in srgb, {palette["accent_2"]} 16%, {palette["paper_alt"]}) 0 14px, color-mix(in srgb, {palette["accent_2"]} 32%, transparent) 15px 16px);
}}
.variant-frostbite-core .plate::before {{
  border-color: color-mix(in srgb, {palette["accent_2"]} 46%, transparent);
}}
.variant-frostbite-core.spread-open .entry-body {{
  border-left: 1px solid color-mix(in srgb, {palette["accent_2"]} 58%, transparent);
  padding-left: 0.18in;
}}
.variant-frostbite-core.spread-open .entry-pullquote {{
  color: {palette["accent_2"]};
  border-top-color: {palette["accent_2"]};
}}
.variant-router-love-letter .plate {{
  background:
    radial-gradient(circle at 22% 42%, color-mix(in srgb, {palette["accent"]} 20%, transparent) 0 14%, transparent 15%),
    repeating-radial-gradient(circle at 82% 24%, transparent 0 18px, color-mix(in srgb, {palette["accent_2"]} 24%, transparent) 19px 20px),
    repeating-linear-gradient(90deg, color-mix(in srgb, {palette["accent_2"]} 34%, transparent) 0 2px, transparent 3px 12px),
    linear-gradient(135deg, {palette["paper_alt"]}, {palette["plate_b"]});
}}
.variant-router-love-letter.spread-open.no-teaser:has(.plate.has-art.art-layout-foldout) {{
  grid-template-columns: minmax(2.52in, 2.82in) minmax(0, 1fr);
}}
.variant-router-love-letter.spread-open.no-teaser:has(.plate.has-art.art-layout-foldout) .plate {{
  min-height: 5.42in;
}}
.variant-router-love-letter.spread-open .entry-pullquote {{
  font-size: 17px;
  color: {palette["accent"]};
}}
.variant-genome-foldout .plate {{
  background:
    repeating-linear-gradient(90deg, transparent 0 14px, color-mix(in srgb, {palette["accent_2"]} 32%, transparent) 15px 16px),
    repeating-linear-gradient(180deg, transparent 0 24px, color-mix(in srgb, {palette["accent_3"]} 22%, transparent) 25px 26px),
    linear-gradient(135deg, {palette["plate_a"]}, {palette["paper_alt"]});
}}
.variant-genome-foldout.spread-open .entry-body,
.variant-genome-foldout.spread-text .entry-body {{
  border-left: 2px solid {palette["accent_3"]};
  padding-left: 0.18in;
}}
.variant-listening-chair {{
  grid-template-columns: 1.9in minmax(0, 1fr);
}}
.variant-listening-chair .plate {{
  background:
    radial-gradient(ellipse at 50% 62%, color-mix(in srgb, {palette["accent_2"]} 30%, transparent) 0 18%, transparent 19%),
    linear-gradient(90deg, transparent 0 46%, color-mix(in srgb, {palette["ink"]} 12%, transparent) 47% 49%, transparent 50%),
    repeating-linear-gradient(180deg, {palette["plate_a"]} 0 18px, {palette["paper_alt"]} 19px 35px);
}}
.variant-listening-chair .entry-body {{
  background:
    repeating-linear-gradient(180deg, transparent 0 24px, color-mix(in srgb, {palette["rule"]} 58%, transparent) 25px 26px);
  padding: 0.04in 0.1in;
}}
.variant-receipt-transcript .plate {{
  background:
    radial-gradient(circle at 18% 26%, {palette["plate_c"]} 0 11%, transparent 12%),
    linear-gradient(90deg, transparent 0 49%, color-mix(in srgb, {palette["ink"]} 14%, transparent) 50%, transparent 51%),
    repeating-linear-gradient(180deg, {palette["plate_a"]} 0 15px, {palette["paper_alt"]} 16px 31px);
}}
.variant-receipt-transcript.spread-open .entry-body,
.variant-receipt-transcript.spread-text .entry-body {{
  background:
    repeating-linear-gradient(180deg, transparent 0 18px, color-mix(in srgb, {palette["rule"]} 56%, transparent) 19px 20px);
}}
.variant-pneumatic-tube .plate {{
  background:
    radial-gradient(circle at 78% 35%, color-mix(in srgb, {palette["accent"]} 30%, transparent) 0 11%, transparent 12%),
    repeating-linear-gradient(90deg, transparent 0 34px, color-mix(in srgb, {palette["accent_2"]} 28%, transparent) 35px 37px),
    linear-gradient(180deg, {palette["paper_alt"]}, {palette["plate_a"]});
}}
.variant-pneumatic-tube.spread-text .entry-body {{
  border-top: 1px solid {palette["rule"]};
  border-bottom: 1px solid {palette["rule"]};
  padding-block: 0.12in;
}}
.variant-detergent-ticket .plate {{
  background:
    radial-gradient(circle at 26% 28%, color-mix(in srgb, {palette["accent_2"]} 48%, transparent) 0 8%, transparent 9%),
    radial-gradient(circle at 70% 63%, color-mix(in srgb, {palette["accent_3"]} 44%, transparent) 0 12%, transparent 13%),
    radial-gradient(circle at 86% 21%, color-mix(in srgb, {palette["accent"]} 30%, transparent) 0 7%, transparent 8%),
    linear-gradient(90deg, transparent 0 18%, color-mix(in srgb, {palette["accent"]} 42%, transparent) 19% 21%, transparent 22% 58%, color-mix(in srgb, {palette["accent_2"]} 36%, transparent) 59% 61%, transparent 62%),
    repeating-linear-gradient(180deg, {palette["paper_alt"]} 0 8px, color-mix(in srgb, {palette["accent"]} 18%, transparent) 9px 10px);
}}
.variant-detergent-ticket.spread-open .entry-body,
.variant-detergent-ticket.spread-text .entry-body {{
  border-color: color-mix(in srgb, {palette["accent"]} 28%, transparent);
}}
.variant-detergent-ticket .plate-grid span {{
  border-style: dashed;
}}
.variant-vhs-echo .plate {{
  background:
    linear-gradient(90deg, color-mix(in srgb, {palette["ink"]} 20%, transparent) 0 5%, transparent 6% 94%, color-mix(in srgb, {palette["ink"]} 20%, transparent) 95%),
    repeating-linear-gradient(180deg, color-mix(in srgb, {palette["accent"]} 20%, transparent) 0 2px, transparent 3px 12px),
    linear-gradient(135deg, {palette["plate_b"]}, {palette["paper_alt"]});
}}
.variant-vhs-echo.spread-text .entry-body {{
  transform: skewY(-0.2deg);
}}
.variant-infinite-windows .plate {{
  background:
    linear-gradient(90deg, transparent 0 47%, color-mix(in srgb, {palette["accent_2"]} 32%, transparent) 48% 49%, transparent 50%),
    linear-gradient(180deg, transparent 0 47%, color-mix(in srgb, {palette["accent"]} 28%, transparent) 48% 49%, transparent 50%),
    repeating-linear-gradient(90deg, {palette["paper_alt"]} 0 42px, color-mix(in srgb, {palette["rule"]} 46%, transparent) 43px 44px);
}}
.variant-ghost-ship .plate {{
  background:
    radial-gradient(ellipse at 64% 64%, color-mix(in srgb, {palette["accent_2"]} 28%, transparent) 0 18%, transparent 19%),
    linear-gradient(140deg, transparent 0 42%, color-mix(in srgb, {palette["accent"]} 24%, transparent) 43% 45%, transparent 46%),
    repeating-linear-gradient(180deg, {palette["paper_alt"]} 0 18px, color-mix(in srgb, {palette["rule"]} 38%, transparent) 19px 20px);
}}
.variant-static-echoes .plate {{
  background:
    repeating-linear-gradient(180deg, transparent 0 8px, color-mix(in srgb, {palette["accent_2"]} 30%, transparent) 9px 10px),
    repeating-radial-gradient(circle at 50% 50%, transparent 0 16px, color-mix(in srgb, {palette["accent_2"]} 24%, transparent) 17px 18px),
    linear-gradient(135deg, {palette["paper_alt"]}, {palette["plate_b"]});
}}
.variant-midway-mind .plate {{
  background:
    radial-gradient(circle at 50% 50%, color-mix(in srgb, {palette["accent_3"]} 40%, transparent) 0 17%, transparent 18%),
    repeating-conic-gradient(from 0.08turn at 50% 50%, color-mix(in srgb, {palette["accent"]} 18%, transparent) 0 6deg, transparent 7deg 18deg),
    linear-gradient(135deg, {palette["plate_a"]}, {palette["paper_alt"]});
}}
.variant-memory-weaver .plate {{
  background:
    repeating-linear-gradient(45deg, transparent 0 15px, color-mix(in srgb, {palette["accent"]} 20%, transparent) 16px 17px),
    repeating-linear-gradient(-45deg, transparent 0 18px, color-mix(in srgb, {palette["accent_2"]} 18%, transparent) 19px 20px),
    linear-gradient(135deg, {palette["plate_a"]}, {palette["paper_alt"]});
}}
.variant-dialup-entanglement .plate {{
  background:
    repeating-linear-gradient(90deg, color-mix(in srgb, {palette["accent_2"]} 30%, transparent) 0 1px, transparent 2px 10px),
    repeating-linear-gradient(180deg, transparent 0 17px, color-mix(in srgb, {palette["accent"]} 18%, transparent) 18px 19px),
    linear-gradient(135deg, {palette["paper_alt"]}, {palette["plate_b"]});
}}
.variant-silent-ward .plate {{
  background:
    linear-gradient(90deg, transparent 0 22%, color-mix(in srgb, {palette["accent_2"]} 32%, transparent) 23% 24%, transparent 25%),
    repeating-linear-gradient(180deg, {palette["paper_alt"]} 0 26px, color-mix(in srgb, {palette["accent"]} 16%, transparent) 27px 28px),
    linear-gradient(135deg, {palette["plate_a"]}, {palette["paper_alt"]});
}}
.variant-silent-ward.spread-open .entry-body,
.variant-silent-ward.spread-text .entry-body {{
  background:
    repeating-linear-gradient(180deg, transparent 0 18px, color-mix(in srgb, {palette["rule"]} 52%, transparent) 19px 20px);
}}
.variant-underworld-ledger .plate {{
  background:
    radial-gradient(circle at 50% 58%, color-mix(in srgb, {palette["accent_3"]} 34%, transparent) 0 20%, transparent 21%),
    repeating-linear-gradient(180deg, {palette["paper_alt"]} 0 20px, color-mix(in srgb, {palette["ink"]} 12%, transparent) 21px 22px),
    linear-gradient(135deg, {palette["plate_a"]}, {palette["paper_alt"]});
}}
.variant-underworld-ledger.spread-open .entry-body,
.variant-underworld-ledger.spread-text .entry-body {{
  border-left: 2px solid {palette["accent_3"]};
  padding-left: 0.18in;
}}
.variant-garment-tag .plate {{
  background:
    linear-gradient(90deg, transparent 0 27%, color-mix(in srgb, {palette["accent"]} 34%, transparent) 28% 30%, transparent 31%),
    repeating-linear-gradient(180deg, {palette["paper_alt"]} 0 13px, color-mix(in srgb, {palette["rule"]} 58%, transparent) 14px 15px),
    repeating-linear-gradient(90deg, transparent 0 32px, color-mix(in srgb, {palette["accent_2"]} 20%, transparent) 33px 34px);
}}
.variant-dock-12 .plate {{
  background:
    radial-gradient(circle at 76% 35%, color-mix(in srgb, {palette["accent_2"]} 45%, transparent) 0 9%, transparent 10%),
    linear-gradient(180deg, transparent 0 42%, color-mix(in srgb, {palette["accent"]} 30%, transparent) 43% 45%, transparent 46%),
    repeating-linear-gradient(90deg, {palette["paper_alt"]} 0 29px, color-mix(in srgb, {palette["ink"]} 16%, transparent) 30px 31px);
}}
.variant-smudged-badge .plate {{
  background:
    radial-gradient(ellipse at 34% 54%, color-mix(in srgb, {palette["ink"]} 18%, transparent) 0 16%, transparent 17%),
    radial-gradient(ellipse at 37% 52%, color-mix(in srgb, {palette["accent"]} 24%, transparent) 0 23%, transparent 24%),
    linear-gradient(90deg, transparent 0 22%, color-mix(in srgb, {palette["rule"]} 68%, transparent) 23% 24%, transparent 25%),
    repeating-linear-gradient(180deg, {palette["paper_alt"]} 0 18px, color-mix(in srgb, {palette["accent_2"]} 16%, transparent) 19px 20px);
}}
.variant-smudged-badge .plate-orbit {{
  width: 1.15in;
  height: 0.42in;
  right: 0.64in;
  bottom: 0.48in;
  transform: rotate(4deg);
}}
.variant-copyist-dilemma .plate {{
  background:
    linear-gradient(90deg, transparent 0 38%, color-mix(in srgb, {palette["ink"]} 14%, transparent) 39% 40%, transparent 41% 62%, color-mix(in srgb, {palette["accent"]} 20%, transparent) 63% 64%, transparent 65%),
    repeating-linear-gradient(180deg, color-mix(in srgb, {palette["paper_alt"]} 92%, transparent) 0 20px, color-mix(in srgb, {palette["rule"]} 54%, transparent) 21px 22px),
    linear-gradient(135deg, {palette["plate_a"]}, {palette["paper_alt"]});
}}
.variant-copyist-dilemma .plate-thread {{
  top: 64%;
  transform: rotate(1.5deg);
  background: color-mix(in srgb, {palette["accent"]} 48%, transparent);
}}
.variant-copyist-dilemma.spread-open .entry-body,
.variant-copyist-dilemma.spread-text .entry-body {{
  border-left: 1px solid color-mix(in srgb, {palette["accent"]} 48%, transparent);
  padding-left: 0.16in;
}}
.variant-ice-bucket-tags .plate {{
  background:
    radial-gradient(circle at 72% 48%, color-mix(in srgb, {palette["accent_2"]} 28%, transparent) 0 15%, transparent 16%),
    repeating-linear-gradient(90deg, transparent 0 0.28in, color-mix(in srgb, {palette["accent"]} 22%, transparent) 0.29in 0.31in),
    repeating-linear-gradient(180deg, {palette["paper_alt"]} 0 16px, color-mix(in srgb, {palette["rule"]} 52%, transparent) 17px 18px);
}}
.variant-ice-bucket-tags .plate-grid {{
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  grid-template-rows: 1fr;
}}
.variant-ice-bucket-tags .plate-grid span {{
  background:
    linear-gradient(180deg, color-mix(in srgb, {palette["accent"]} 16%, transparent), transparent 58%),
    color-mix(in srgb, {palette["paper_alt"]} 76%, transparent);
}}
.variant-clipboard-edge .plate {{
  background:
    linear-gradient(112deg, transparent 0 37%, color-mix(in srgb, {palette["accent"]} 42%, transparent) 38% 40%, transparent 41%),
    repeating-linear-gradient(180deg, {palette["paper_alt"]} 0 17px, color-mix(in srgb, {palette["accent_2"]} 16%, transparent) 18px 19px),
    repeating-linear-gradient(90deg, transparent 0 38px, color-mix(in srgb, {palette["ink"]} 10%, transparent) 39px 40px);
}}
.variant-clipboard-edge.spread-open .entry-body,
.variant-clipboard-edge.spread-text .entry-body {{
  border-top: 1px solid color-mix(in srgb, {palette["rule"]} 78%, transparent);
  border-bottom: 1px solid color-mix(in srgb, {palette["rule"]} 78%, transparent);
  padding-block: 0.12in;
}}
.variant-saved-fingerprint .plate {{
  background:
    repeating-radial-gradient(ellipse at 46% 52%, transparent 0 9px, color-mix(in srgb, {palette["accent_2"]} 28%, transparent) 10px 11px),
    linear-gradient(90deg, transparent 0 48%, color-mix(in srgb, {palette["accent"]} 30%, transparent) 49% 50%, transparent 51%),
    linear-gradient(135deg, {palette["paper_alt"]}, {palette["plate_b"]});
}}
.variant-saved-fingerprint.spread-open .entry-body,
.variant-saved-fingerprint.spread-text .entry-body {{
  border-left: 1px solid color-mix(in srgb, {palette["accent_2"]} 52%, transparent);
  padding-left: 0.16in;
}}
.variant-spiral-file .plate {{
  background:
    repeating-conic-gradient(from 0.02turn at 50% 48%, color-mix(in srgb, {palette["accent"]} 24%, transparent) 0 8deg, transparent 9deg 22deg),
    radial-gradient(circle at 50% 48%, color-mix(in srgb, {palette["accent_3"]} 30%, transparent) 0 13%, transparent 14%),
    linear-gradient(135deg, {palette["plate_a"]}, {palette["paper_alt"]});
}}
.variant-velvet-audit .plate {{
  background:
    radial-gradient(ellipse at 42% 58%, color-mix(in srgb, {palette["accent"]} 28%, transparent) 0 20%, transparent 21%),
    repeating-linear-gradient(110deg, color-mix(in srgb, {palette["accent_3"]} 18%, transparent) 0 7px, transparent 8px 18px),
    linear-gradient(135deg, {palette["plate_a"]}, {palette["paper_alt"]});
}}
.variant-final-notice .plate {{
  background:
    linear-gradient(90deg, color-mix(in srgb, {palette["accent"]} 34%, transparent) 0 13%, transparent 14%),
    repeating-linear-gradient(180deg, {palette["paper_alt"]} 0 21px, color-mix(in srgb, {palette["ink"]} 14%, transparent) 22px 23px),
    linear-gradient(135deg, {palette["plate_a"]}, {palette["paper_alt"]});
}}
.spread-open.no-teaser {{
  grid-template-rows: auto minmax(1.48in, 1.84in) minmax(0, 1fr) auto;
  grid-template-columns: 1fr;
  grid-template-areas:
    "head"
    "plate"
    "body"
    "foot";
}}
.spread-open.no-teaser .entry-body {{
  max-width: var(--story-body-width, 5.28in);
  margin: 0 auto;
  align-self: start;
}}
.spread-open.no-teaser.protocol .entry-body {{
  max-width: var(--story-body-width, 5.16in);
}}
.spread-text {{
  grid-template-columns: 1fr;
  grid-template-areas:
    "head"
    "body"
    "foot";
  grid-template-rows: auto minmax(0, 1fr) auto;
  gap: 0.18in;
}}
.spread-text .plate,
.spread-text .entry-pullquote {{
  display: none;
}}
.spread-text .entry-head {{
  align-items: start;
  border-bottom: 1px solid {palette["rule"]};
  padding-bottom: 0.14in;
}}
.spread-text .byte {{
  font-size: 22px;
}}
.spread-text h3 {{
  font-size: 23px;
  line-height: 1.05;
}}
.spread-text h3 span {{
  color: {palette["muted"]};
  font-family: {typo["mono"]};
  font-size: 9px;
  letter-spacing: 0.12em;
  text-transform: uppercase;
}}
.spread-text .entry-body {{
  max-width: var(--story-body-width, 5.38in);
  margin: 0 auto;
  font-size: var(--story-body-size, 11.1px);
  line-height: var(--story-body-leading, 1.52);
  align-self: center;
}}
.spread-text.tail-continuation {{
  grid-template-columns: minmax(1.22in, 1.58in) minmax(0, 1fr);
  grid-template-areas:
    "head head"
    "plate body"
    "foot foot";
  grid-template-rows: auto minmax(0, 1fr) auto;
  column-gap: 0.22in;
  row-gap: 0.16in;
}}
.spread-text.tail-continuation .entry-body {{
  align-self: center;
  max-width: none;
  margin: 0;
}}
.spread-text.tail-continuation .plate {{
  display: grid;
  min-height: 4.25in;
  height: 100%;
  width: 100%;
  max-width: none;
  margin: 0;
  opacity: 0.82;
}}
.spread-text.tail-continuation .plate-sigil {{
  font-size: 38px;
}}
.spread-text.tail-continuation .plate-label {{
  font-size: 6.4px;
  left: 0.13in;
  right: 0.13in;
  bottom: 0.14in;
  max-width: none;
}}
.spread-text.tail-continuation.tail-compact {{
  grid-template-columns: minmax(1.44in, 1.86in) minmax(0, 1fr);
}}
.spread-text.tail-continuation.tail-compact .entry-body {{
  align-self: center;
  font-size: calc(var(--story-body-size, 11.1px) * 1.03);
  line-height: var(--story-body-leading, 1.54);
}}
.spread-text.tail-continuation.tail-compact .plate {{
  min-height: 4.75in;
}}
.spread-text.tail-continuation.protocol .entry-body {{
  padding: 0.16in;
}}
.spread-text.tail-continuation.archive .entry-body {{
  padding-block: 0.11in;
}}
.spread-text.art-continuation {{
  grid-template-columns: minmax(1.36in, 1.72in) minmax(0, 1fr);
  grid-template-areas:
    "head head"
    "plate body"
    "foot foot";
  grid-template-rows: auto minmax(0, 1fr) auto;
  column-gap: 0.22in;
  row-gap: 0.16in;
}}
.spread-text.art-continuation .plate {{
  display: grid;
  grid-area: plate;
  align-self: stretch;
  min-height: 6.22in;
  height: 100%;
  width: 100%;
  max-width: none;
  margin: 0;
  opacity: 0.82;
}}
.spread-text.art-continuation .entry-body {{
  align-self: center;
  max-width: none;
  margin: 0;
}}
.spread-text.art-continuation .plate-sigil {{
  font-size: 30px;
}}
.spread-text.art-continuation .plate-label {{
  font-size: 5.6px;
}}
.spread-text.art-continuation:has(.plate.has-art.art-layout-continuation-strip),
.spread-text.art-continuation:has(.plate.has-art.art-layout-receipt-strip) {{
  grid-template-columns: 1fr;
  grid-template-areas:
    "head"
    "plate"
    "body"
    "foot";
  grid-template-rows: auto 0.86in minmax(0, 1fr) auto;
  row-gap: 0.11in;
}}
.spread-text.art-continuation:has(.plate.has-art.art-layout-continuation-strip) .plate,
.spread-text.art-continuation:has(.plate.has-art.art-layout-receipt-strip) .plate {{
  min-height: 0.86in;
  height: 0.86in;
  opacity: 0.88;
}}
.spread-text.art-continuation:has(.plate.has-art.art-layout-continuation-strip) .entry-body,
.spread-text.art-continuation:has(.plate.has-art.art-layout-receipt-strip) .entry-body {{
  max-width: 5.38in;
  margin: 0 auto;
  align-self: start;
}}
.spread-text.art-continuation:has(.plate.has-art.art-layout-continuation-strip) .plate-sigil,
.spread-text.art-continuation:has(.plate.has-art.art-layout-continuation-strip) .plate-label,
.spread-text.art-continuation:has(.plate.has-art.art-layout-continuation-strip) .plate-notes,
.spread-text.art-continuation:has(.plate.has-art.art-layout-receipt-strip) .plate-sigil,
.spread-text.art-continuation:has(.plate.has-art.art-layout-receipt-strip) .plate-label,
.spread-text.art-continuation:has(.plate.has-art.art-layout-receipt-strip) .plate-notes {{
  display: none;
}}
.spread-text.variant-frostbite-core.art-continuation {{
  grid-template-columns: minmax(0, 1fr);
  grid-template-areas:
    "head"
    "plate"
    "body"
    "foot";
  grid-template-rows: auto 0.86in minmax(0, 1fr) auto;
  row-gap: 0.1in;
}}
.spread-text.variant-frostbite-core.art-continuation .plate {{
  min-height: 0.86in;
  height: 0.86in;
  width: 100%;
  opacity: 0.72;
}}
.spread-text.variant-frostbite-core.art-continuation .entry-body {{
  max-width: 5.28in;
  width: 100%;
  margin: 0 auto;
  font-size: var(--story-body-size, 8.15px);
  line-height: 1.32;
}}
.spread-text.signal .entry-body {{
  font-size: var(--story-body-size, 8.8px);
  line-height: var(--story-body-leading, 1.5);
}}
.spread-text.protocol .entry-body {{
  font-size: var(--story-body-size, 9.55px);
  line-height: var(--story-body-leading, 1.45);
  padding: 0.18in;
}}
.spread-text.myth .entry-body {{
  font-size: var(--story-body-size, 12.7px);
  line-height: var(--story-body-leading, 1.5);
}}
.spread-text.glitch .entry-body {{
  font-size: var(--story-body-size, 8.25px);
  line-height: var(--story-body-leading, 1.43);
  max-height: none;
  overflow: hidden;
}}
.spread-text.glitch {{
  display: grid;
  grid-template-columns: 1fr;
  grid-template-areas:
    "head"
    "body"
    "foot";
  grid-template-rows: auto minmax(0, 1fr) auto;
  gap: 0.16in;
}}
.spread-text.glitch .entry-head {{
  margin-bottom: 0;
}}
.spread-text.glitch .entry-body {{
  align-self: start;
  margin: 0 auto;
  max-width: var(--story-body-width, 5.25in);
}}
.spread-text.glitch .entry-foot {{
  margin-top: 0;
}}
.spread-text.glitch.art-continuation {{
  grid-template-columns: 1fr;
  grid-template-areas:
    "head"
    "plate"
    "body"
    "foot";
  grid-template-rows: auto 1.74in minmax(0, 1fr) auto;
  gap: 0.14in;
}}
.spread-text.glitch.art-continuation .plate {{
  min-height: 1.74in;
  height: 1.74in;
  width: 100%;
  opacity: 0.78;
}}
.spread-text.glitch.art-continuation .entry-body {{
  align-self: center;
  max-width: 5.24in;
  margin: 0 auto;
}}
.spread-text.glitch.tail-continuation {{
  grid-template-columns: minmax(1.22in, 1.58in) minmax(0, 1fr);
  grid-template-areas:
    "head head"
    "plate body"
    "foot foot";
  grid-template-rows: auto minmax(0, 1fr) auto;
  column-gap: 0.22in;
  row-gap: 0.16in;
}}
.spread-text.glitch.tail-continuation .entry-body {{
  align-self: center;
  max-width: none;
  margin: 0;
}}
.spread-text.glitch.tail-continuation .plate {{
  display: grid;
  min-height: 4.25in;
  height: 100%;
  width: 100%;
  max-width: none;
  margin: 0;
  opacity: 0.82;
}}
.entry.glitch.art-quad.spread-text {{
  grid-template-rows: auto minmax(0, 1fr) auto;
}}
.entry.glitch.art-quad.spread-text.art-continuation {{
  grid-template-rows: auto 1.74in minmax(0, 1fr) auto;
}}
.entry.glitch.variant-clipboard-edge.spread-text.art-continuation {{
  grid-template-rows: auto 0.86in minmax(0, 1fr) auto;
}}
.entry.glitch.variant-clipboard-edge.spread-text.art-continuation .plate {{
  min-height: 0.86in;
  height: 0.86in;
  opacity: 0.88;
}}
.entry.glitch.variant-clipboard-edge.spread-text.art-continuation .entry-body {{
  align-self: start;
}}
.entry.glitch.art-quad.spread-text.tail-continuation {{
  grid-template-rows: auto minmax(0, 1fr) auto;
}}
.caption {{
  color: {palette["muted"]};
  font-family: {typo["sans"]};
  font-size: 7.6px;
  line-height: 1.32;
  min-width: 0;
  overflow-wrap: anywhere;
  text-transform: uppercase;
  letter-spacing: 0.06em;
}}
.caption .caption-accent {{
  color: {palette["accent_2"]};
  font-family: {typo["mono"]};
}}
.caption .caption-state {{
  color: {palette["accent"]};
}}
.qr {{
  width: 0.78in;
  height: 0.78in;
  box-sizing: border-box;
  border: 1px solid color-mix(in srgb, {palette["ink"]} 72%, transparent);
  display: block;
  position: relative;
  background: #fff;
  padding: 0.035in;
  color: #000;
  text-decoration: none;
}}
.qr-code {{
  display: block;
  width: 100%;
  height: 100%;
  color: #000;
  shape-rendering: crispEdges;
}}
.qr-modules {{
  fill: #000;
}}
.toc h2, .notes h2, .object-page h2 {{
  font-family: {typo["display"]};
  font-size: 34px;
  font-weight: 400;
}}
.object-page {{
  display: grid;
  grid-template-rows: auto auto minmax(0, 1fr) auto;
  gap: 0.16in;
}}
.object-page h2 {{
  margin: 0;
  line-height: 0.96;
}}
.object-kicker {{
  font-family: {typo["mono"]};
  font-size: 8px;
  color: {palette["accent"]};
  text-transform: uppercase;
  letter-spacing: 0.16em;
}}
.object-columns {{
  columns: 2;
  column-gap: 0.28in;
  max-width: 5.45in;
  align-self: start;
}}
.object-columns p {{
  margin: 0 0 0.12in;
  font-size: 12.2px;
  line-height: 1.48;
}}
.how-read {{
  grid-template-rows: auto auto minmax(0, 1fr) auto auto;
  gap: 0.14in;
}}
.how-read h2 {{
  max-width: 4.8in;
  font-size: 46px;
  line-height: 0.92;
}}
.reading-oracle {{
  display: grid;
  grid-template-columns: 1.12in minmax(0, 1fr);
  gap: 0.18in;
  min-height: 0;
  align-self: stretch;
}}
.reading-sigil {{
  border-right: 1px solid {palette["rule"]};
  padding-right: 0.14in;
  font-family: {typo["mono"]};
  color: {palette["accent"]};
  font-size: 38px;
  line-height: 0.98;
  letter-spacing: 0.08em;
  word-break: break-all;
}}
.reading-body {{
  display: grid;
  grid-template-rows: auto auto 1fr;
  gap: 0.12in;
  min-height: 0;
}}
.reading-lede {{
  margin: 0;
  max-width: 4.55in;
  font-family: {typo["display"]};
  font-size: 23px;
  line-height: 1.18;
  color: {palette["ink"]};
}}
.reading-body p {{
  margin: 0;
  max-width: 4.8in;
  font-size: 12.7px;
  line-height: 1.48;
}}
.reading-hints {{
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0.09in 0.14in;
  align-self: end;
}}
.reading-hints div {{
  min-height: 0.68in;
  border-top: 1px solid {palette["rule"]};
  padding-top: 0.055in;
}}
.reading-hints strong {{
  display: block;
  font-family: {typo["mono"]};
  color: {palette["accent_2"]};
  font-size: 7px;
  line-height: 1.25;
  text-transform: uppercase;
  letter-spacing: 0.12em;
}}
.reading-hints span {{
  display: block;
  margin-top: 0.04in;
  font-size: 9.6px;
  line-height: 1.35;
  color: {palette["muted"]};
}}
.reading-register {{
  display: grid;
  grid-template-columns: repeat(8, minmax(0, 1fr));
  gap: 0.035in;
  align-self: end;
}}
.reading-register span {{
  min-height: 0.38in;
  border: 1px solid color-mix(in srgb, {palette["rule"]} 76%, transparent);
  display: grid;
  place-items: center;
  font-family: {typo["mono"]};
  font-size: 6px;
  color: color-mix(in srgb, {palette["muted"]} 72%, transparent);
  background:
    linear-gradient(135deg, color-mix(in srgb, {palette["paper_alt"]} 68%, transparent), transparent);
}}
.reading-register span:nth-child(3n + 1) {{
  color: {palette["accent"]};
  border-color: color-mix(in srgb, {palette["accent"]} 46%, transparent);
}}
.reading-register span:nth-child(3n + 2) {{
  color: {palette["accent_2"]};
  border-color: color-mix(in srgb, {palette["accent_2"]} 42%, transparent);
}}
.artifact-band {{
  align-self: end;
  display: grid;
  grid-template-columns: 0.9in 1fr 1.3in;
  gap: 0.08in;
  border-top: 1px solid {palette["rule"]};
  border-bottom: 1px solid {palette["rule"]};
  padding: 0.09in 0;
  font-family: {typo["mono"]};
  font-size: 7px;
  line-height: 1.35;
  color: {palette["muted"]};
  text-transform: uppercase;
  letter-spacing: 0.08em;
}}
.artifact-band span:first-child {{
  color: {palette["accent"]};
}}
.taxonomy-page {{
  grid-template-rows: auto auto auto minmax(0, 1fr) auto auto;
}}
.taxonomy-spectrum {{
  position: relative;
  isolation: isolate;
  display: grid;
  grid-template-columns: repeat(16, 1fr);
  gap: 0.018in;
  height: 0.31in;
  margin-top: -0.01in;
  margin-bottom: 0.09in;
  padding: 0.035in 0.04in;
  overflow: hidden;
  border-top: 1px solid color-mix(in srgb, {palette["rule"]} 70%, transparent);
  border-bottom: 1px solid color-mix(in srgb, {palette["rule"]} 54%, transparent);
  background:
    linear-gradient(90deg, color-mix(in srgb, {palette["paper_alt"]} 54%, transparent), transparent 24%, transparent 76%, color-mix(in srgb, {palette["paper_alt"]} 50%, transparent)),
    repeating-linear-gradient(90deg, transparent 0 0.19in, color-mix(in srgb, {palette["rule"]} 22%, transparent) 0.19in 0.196in);
}}
.taxonomy-spectrum span {{
  --lift: 0in;
  --tilt: 0deg;
  position: relative;
  height: 100%;
  margin-left: 0;
  border: 1px solid color-mix(in srgb, var(--s-accent-2) 42%, transparent);
  background:
    radial-gradient(circle at 22% 26%, color-mix(in srgb, var(--s-accent-3) 74%, transparent), transparent 28%),
    linear-gradient(135deg, color-mix(in srgb, var(--s-accent) 78%, transparent), color-mix(in srgb, var(--s-accent-2) 46%, transparent)),
    var(--s-wash);
  opacity: 0.74;
  mix-blend-mode: {spectrum_blend};
  transform: none;
  box-shadow:
    0 0 0.12in color-mix(in srgb, var(--s-accent) 16%, transparent),
    inset 0 0 0.12in color-mix(in srgb, {palette["paper"]} 30%, transparent);
}}
.taxonomy-spectrum span::before {{
  content: attr(data-code);
  position: absolute;
  left: 0.035in;
  bottom: 0.025in;
  font-family: {typo["mono"]};
  font-size: 5.4px;
  line-height: 1;
  color: color-mix(in srgb, {palette["ink"]} 72%, transparent);
  opacity: 0.7;
}}
.taxonomy-spectrum span::after {{
  content: "";
  position: absolute;
  inset: 0;
  background:
    repeating-linear-gradient(90deg, transparent 0 5px, color-mix(in srgb, {palette["paper"]} 20%, transparent) 5px 6px),
    linear-gradient(180deg, color-mix(in srgb, {palette["paper"]} 18%, transparent), transparent 55%, color-mix(in srgb, {palette["ink"]} 10%, transparent));
  opacity: 0.48;
  pointer-events: none;
}}
.taxonomy-spectrum span:nth-child(4n + 1) {{
  --lift: 0in;
  --tilt: 0deg;
}}
.taxonomy-spectrum span:nth-child(4n + 2) {{
  --lift: 0in;
  --tilt: 0deg;
}}
.taxonomy-spectrum span:nth-child(4n + 3) {{
  --lift: 0in;
  --tilt: 0deg;
}}
.taxonomy-spectrum span:nth-child(4n) {{
  --lift: 0in;
  --tilt: 0deg;
}}
.taxonomy-grid {{
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0.11in 0.12in;
  min-height: 0;
}}
.mode-card {{
  --mode-accent: {palette["accent"]};
  --mode-accent-2: {palette["accent_2"]};
  --mode-accent-3: {palette["accent_3"]};
  --mode-wash: {palette["paper_alt"]};
  display: block;
  border: 1px solid color-mix(in srgb, var(--mode-accent) 38%, {palette["rule"]});
  border-top: 2px solid color-mix(in srgb, var(--mode-accent) 70%, {palette["rule"]});
  padding: 0.16in 0.18in 0.14in;
  min-height: 1.42in;
  position: relative;
  overflow: hidden;
  background:
    linear-gradient(135deg, color-mix(in srgb, var(--mode-wash) 72%, transparent), transparent 72%),
    color-mix(in srgb, {palette["paper_alt"]} 86%, var(--mode-wash));
}}
.mode-card-panel {{
  position: absolute;
  inset: 0;
  overflow: hidden;
  border: 0;
  background:
    linear-gradient(135deg, color-mix(in srgb, var(--mode-accent) 18%, transparent), transparent),
    {palette["paper_alt"]};
  z-index: 0;
}}
.mode-card-panel img {{
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
  filter: saturate(0.82) contrast(0.96);
  opacity: {0.34 if palette_name == "light" else 0.26};
  mix-blend-mode: {mode_panel_blend};
}}
.mode-card::after {{
  content: "";
  position: absolute;
  inset: 0;
  z-index: 1;
  pointer-events: none;
  background:
    linear-gradient(90deg, color-mix(in srgb, {palette["paper"]} 88%, transparent) 0 44%, transparent 76%),
    linear-gradient(180deg, color-mix(in srgb, {palette["paper"]} 70%, transparent), transparent 58%, color-mix(in srgb, var(--mode-accent) 12%, transparent));
}}
.mode-card-body {{
  position: relative;
  z-index: 2;
  min-width: 0;
  max-width: 2.28in;
}}
.mode-card-glyph {{
  position: absolute;
  right: -0.03in;
  top: -0.04in;
  font-family: {typo["mono"]};
  color: color-mix(in srgb, var(--mode-accent-2) 34%, transparent);
  font-size: 28px;
  line-height: 1;
}}
.mode-card h3 {{
  margin: 0 0 0.05in;
  font-family: {typo["display"]};
  font-size: 23px;
  line-height: 0.95;
  font-weight: 400;
  color: {palette["ink"]};
}}
.mode-card p {{
  margin: 0;
  font-size: 8.2px;
  line-height: 1.34;
  max-width: 1.92in;
}}
.mode-card-meta {{
  margin-top: 0.06in;
  font-family: {typo["mono"]};
  color: var(--mode-accent);
  font-size: 5.7px;
  line-height: 1.3;
  text-transform: uppercase;
  letter-spacing: 0.08em;
}}
.mode-card-colors {{
  display: grid;
  grid-template-columns: repeat(4, 0.24in);
  gap: 0.02in;
  margin-top: 0.075in;
}}
.mode-card-colors span {{
  height: 0.075in;
  background: var(--chip);
  border: 1px solid color-mix(in srgb, {palette["ink"]} 20%, transparent);
  box-shadow:
    inset 0 0 0 1px color-mix(in srgb, {palette["paper"]} 24%, transparent),
    0 0.018in 0.04in color-mix(in srgb, {palette["ink"]} 8%, transparent);
}}
.mode-card-colors span:nth-child(4) {{
  background:
    repeating-linear-gradient(90deg, transparent 0 4px, color-mix(in srgb, {palette["ink"]} 12%, transparent) 4px 5px),
    var(--chip);
}}
.taxonomy-thread {{
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 0.055in;
  align-self: end;
}}
.taxonomy-thread span {{
  min-height: 0.38in;
  --mode-accent: {palette["accent"]};
  --mode-accent-2: {palette["accent_2"]};
  --mode-accent-3: {palette["accent_3"]};
  --mode-wash: {palette["paper_alt"]};
  position: relative;
  overflow: hidden;
  border: 1px solid color-mix(in srgb, var(--mode-accent) 58%, {palette["rule"]});
  display: grid;
  place-items: center;
  font-family: {typo["mono"]};
  font-size: 7px;
  color: color-mix(in srgb, var(--mode-accent) 78%, {palette["ink"]});
  letter-spacing: 0.12em;
  text-transform: uppercase;
  background:
    linear-gradient(90deg,
      color-mix(in srgb, var(--mode-accent) 58%, transparent) 0 24%,
      color-mix(in srgb, var(--mode-accent-2) 52%, transparent) 24% 49%,
      color-mix(in srgb, var(--mode-accent-3) 50%, transparent) 49% 74%,
      color-mix(in srgb, var(--mode-wash) 72%, transparent) 74% 100%),
    color-mix(in srgb, var(--mode-wash) 50%, {palette["paper_alt"]});
  transform: none;
  box-shadow:
    inset 0 0 0.16in color-mix(in srgb, {palette["paper"]} 28%, transparent),
    0 0 0.1in color-mix(in srgb, var(--mode-accent) 12%, transparent);
}}
.taxonomy-thread span::after {{
  content: "";
  position: absolute;
  inset: 0;
  background:
    repeating-linear-gradient(90deg, transparent 0 7px, color-mix(in srgb, {palette["ink"]} 12%, transparent) 7px 8px),
    repeating-linear-gradient(0deg, transparent 0 13px, color-mix(in srgb, var(--mode-accent-3) 12%, transparent) 13px 14px);
  opacity: 0.48;
  pointer-events: none;
}}
.taxonomy-thread span strong {{
  position: relative;
  z-index: 1;
  font-weight: 400;
  transform: none;
}}
.taxonomy-thread span:nth-child(odd) {{
  transform: none;
}}
.taxonomy-thread span:nth-child(odd) strong {{
  transform: none;
}}
.packet-note {{
  max-width: 4.6in;
  margin: 0;
  align-self: end;
}}
.certificate-page {{
  grid-template-rows: auto auto minmax(0, 1fr) auto auto;
}}
.certificate-grid {{
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0.1in;
  align-self: start;
  max-width: 5.35in;
}}
.certificate-grid div {{
  border-top: 1px solid {palette["rule"]};
  padding-top: 0.07in;
  min-height: 0.58in;
}}
.certificate-grid span {{
  display: block;
  font-family: {typo["mono"]};
  font-size: 6px;
  color: {palette["muted"]};
  text-transform: uppercase;
  letter-spacing: 0.12em;
}}
.certificate-grid strong {{
  display: block;
  margin-top: 0.035in;
  font-family: {typo["display"]};
  font-size: 19px;
  line-height: 1;
  font-weight: 400;
  color: {palette["ink"]};
}}
.certificate-seal {{
  align-self: center;
  justify-self: center;
  width: 1.36in;
  height: 1.36in;
  border: 1px solid {palette["accent"]};
  border-radius: 999px;
  display: grid;
  place-items: center;
  text-align: center;
  font-family: {typo["mono"]};
  color: {palette["accent"]};
  font-size: 16px;
  line-height: 1.1;
  letter-spacing: 0.12em;
  background:
    radial-gradient(circle, transparent 0 47%, color-mix(in srgb, {palette["accent"]} 18%, transparent) 48% 49%, transparent 50%),
    color-mix(in srgb, {palette["paper_alt"]} 68%, transparent);
}}
.back-cover {{
  display: grid;
  grid-template-rows: auto 1fr auto;
  gap: 0.24in;
  background:
    linear-gradient(135deg, color-mix(in srgb, {palette["accent"]} 18%, transparent), transparent 38%),
    repeating-linear-gradient(90deg, transparent 0 15px, color-mix(in srgb, {palette["rule"]} 38%, transparent) 16px 17px),
    linear-gradient(180deg, {palette["paper_alt"]}, {palette["paper"]});
}}
.back-cover-top {{
  display: flex;
  justify-content: space-between;
  align-items: start;
  gap: 0.2in;
}}
.back-cover-code {{
  font-family: {typo["mono"]};
  font-size: 8px;
  color: {palette["muted"]};
  letter-spacing: 0.14em;
  text-transform: uppercase;
  text-align: right;
  line-height: 1.45;
}}
.back-cover h2 {{
  margin: 0 0 0.18in;
  max-width: 3.7in;
  font-family: {typo["display"]};
  font-size: 54px;
  line-height: 0.9;
  font-weight: 400;
}}
.back-cover-blurb {{
  max-width: 4.45in;
  margin: 0;
  font-size: 15px;
  line-height: 1.44;
}}
.back-cover-meta {{
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 0.08in;
  border-top: 1px solid {palette["rule"]};
  padding-top: 0.1in;
  font-family: {typo["mono"]};
  color: {palette["accent"]};
  font-size: 7px;
  line-height: 1.3;
  text-transform: uppercase;
  letter-spacing: 0.09em;
}}
.spine-code {{
  position: absolute;
  right: 0.18in;
  top: 0.52in;
  bottom: 0.58in;
  writing-mode: vertical-rl;
  transform: rotate(180deg);
  font-family: {typo["mono"]};
  font-size: 7px;
  color: color-mix(in srgb, {palette["muted"]} 72%, transparent);
  letter-spacing: 0.16em;
  text-transform: uppercase;
}}
.mode-index, .section-index {{
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0.08in 0.16in;
  align-self: start;
  min-height: 0;
}}
.mode-index div, .section-index div {{
  border-top: 1px solid {palette["rule"]};
  padding-top: 0.055in;
  min-height: 0.46in;
}}
.mode-index strong, .section-index strong {{
  display: block;
  font-family: {typo["mono"]};
  color: {palette["accent"]};
  font-size: 7px;
  line-height: 1.25;
  text-transform: uppercase;
  letter-spacing: 0.08em;
}}
.mode-index span, .section-index span, .mode-index em {{
  display: block;
  margin-top: 0.03in;
  font-family: {typo["mono"]};
  color: {palette["muted"]};
  font-size: 6.4px;
  line-height: 1.3;
  font-style: normal;
  overflow-wrap: anywhere;
}}
.section-index.compact {{
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0.045in 0.12in;
}}
.section-index.compact div {{
  min-height: 0.28in;
  padding-top: 0.035in;
}}
.lineage-grid {{
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 0.035in 0.07in;
  align-self: start;
  font-family: {typo["mono"]};
  font-size: 5.7px;
  line-height: 1.2;
  color: {palette["muted"]};
}}
.lineage-grid span {{
  border-top: 1px solid color-mix(in srgb, {palette["rule"]} 72%, transparent);
  padding-top: 0.025in;
  overflow-wrap: anywhere;
}}
.lineage-grid strong {{
  color: {palette["accent_2"]};
}}
.toc {{
  height: {trim["height"]};
  display: grid;
  grid-template-rows: auto auto auto 1fr;
  gap: 0.08in;
  padding-bottom: 0.74in;
  background:
    linear-gradient(180deg, {palette["paper"]}, {palette["paper_alt"]}),
    repeating-linear-gradient(90deg, transparent 0 0.33in, color-mix(in srgb, {palette["rule"]} 34%, transparent) 0.335in 0.34in);
}}
.toc h2 {{
  margin: 0;
  line-height: 0.95;
}}
.toc-register {{
  display: grid;
  grid-template-columns: 1.1in 1fr 1.24in;
  gap: 0.08in;
  border-top: 1px solid {palette["rule"]};
  border-bottom: 1px solid {palette["rule"]};
  padding: 0.07in 0;
  font-family: {typo["mono"]};
  color: {palette["muted"]};
  font-size: 6.6px;
  line-height: 1.28;
  text-transform: uppercase;
  letter-spacing: 0.08em;
}}
.toc-register span:first-child {{
  color: {palette["accent"]};
}}
.toc-register span:last-child {{
  color: {palette["accent_2"]};
}}
.notes {{
  display: grid;
  grid-template-rows: auto auto minmax(0, 1fr) auto;
  gap: 0.12in;
}}
.notes h2 {{
  margin: 0;
  line-height: 0.98;
}}
.notes p {{
  margin: 0;
  max-width: 5.1in;
  font-size: 11px;
  line-height: 1.38;
}}
.notes ul {{
  columns: 2;
  column-gap: 0.24in;
  margin: 0;
  padding-left: 0.14in;
  font-size: 7.2px;
  line-height: 1.22;
}}
.notes li {{
  break-inside: avoid;
  margin-bottom: 0.035in;
}}
.front-note {{
  display: grid;
  grid-template-columns: 0.62in minmax(0, 1fr);
  grid-template-rows: auto auto minmax(0, 1fr) auto;
  column-gap: 0.24in;
  row-gap: 0.16in;
  background:
    linear-gradient(90deg, color-mix(in srgb, {palette["paper_alt"]} 82%, transparent) 0 0.74in, transparent 0.74in),
    linear-gradient(150deg, {palette["paper"]}, {palette["paper_alt"]});
}}
.front-note h2 {{
  grid-column: 2;
  font-family: {typo["display"]};
  font-size: 40px;
  line-height: 0.98;
  font-weight: 400;
  margin: 0;
}}
.front-note .note-body {{
  grid-column: 2;
  align-self: stretch;
  max-width: 5.05in;
  display: flex;
  flex-direction: column;
  justify-content: center;
}}
.front-note .note-body p {{
  margin: 0 0 0.13in;
  font-size: 12.9px;
  line-height: 1.5;
}}
.front-note .note-body p:first-child {{
  font-family: {typo["display"]};
  font-size: 21px;
  line-height: 1.22;
  color: {palette["ink"]};
}}
.front-note .note-kicker {{
  grid-column: 2;
  font-family: {typo["mono"]};
  font-size: 7.5px;
  color: {palette["accent"]};
  text-transform: uppercase;
  letter-spacing: 0.16em;
}}
.note-rail {{
  grid-column: 1;
  grid-row: 1 / 5;
  display: grid;
  align-content: space-between;
  justify-items: center;
  min-height: 100%;
  border-right: 1px solid color-mix(in srgb, {palette["rule"]} 72%, transparent);
  padding-right: 0.12in;
  font-family: {typo["mono"]};
  color: {palette["accent"]};
  text-transform: uppercase;
  letter-spacing: 0.16em;
}}
.note-rail strong {{
  writing-mode: vertical-rl;
  transform: rotate(180deg);
  font-size: 10px;
  line-height: 1;
}}
.front-note-machines .note-rail {{
  padding-right: 0.08in;
}}
.front-note-machines .note-rail strong {{
  font-size: 8.6px;
  letter-spacing: 0.09em;
}}
.note-rail span {{
  width: 0.34in;
  height: 0.34in;
  display: grid;
  place-items: center;
  border: 1px solid color-mix(in srgb, {palette["accent"]} 58%, transparent);
  font-size: 7px;
  letter-spacing: 0;
}}
.note-register {{
  grid-column: 2;
  align-self: end;
  display: grid;
  grid-template-columns: 0.8in 1fr 0.95in;
  gap: 0.06in;
  border-top: 1px solid {palette["rule"]};
  border-bottom: 1px solid {palette["rule"]};
  padding: 0.08in 0;
  font-family: {typo["mono"]};
  font-size: 6.4px;
  line-height: 1.3;
  color: {palette["muted"]};
  text-transform: uppercase;
  letter-spacing: 0.08em;
}}
.note-register span:first-child {{
  color: {palette["accent_2"]};
}}
.front-mark {{
  position: absolute;
  top: 0.52in;
  right: 0.52in;
  width: 0.32in;
  height: 0.32in;
  object-fit: contain;
  opacity: 0.42;
  {mark_filter}
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


def pdf_profile_css(profile_name: str) -> str:
    if profile_name == "review":
        return ""
    css = """
.pdf-profile-print *,
.pdf-profile-download * {
  mix-blend-mode: normal !important;
  backdrop-filter: none !important;
}
.pdf-profile-print .plate-art-img,
.pdf-profile-download .plate-art-img,
.pdf-profile-print .mode-card-panel img,
.pdf-profile-download .mode-card-panel img,
.pdf-profile-print .section-panel img,
.pdf-profile-download .section-panel img {
  filter: none !important;
  -webkit-mask-image: none !important;
  mask-image: none !important;
}
""".strip()
    if profile_name != "download":
        return css
    download_css = """
.pdf-profile-download .plate.has-art::after,
.pdf-profile-download .section-panel::after,
.pdf-profile-download .mode-card::after,
.pdf-profile-download .taxonomy-spectrum span::after,
.pdf-profile-download .taxonomy-thread span::after {
  display: none !important;
}
.pdf-profile-download .page,
.pdf-profile-download .plate {
  background: var(--paper) !important;
}
.pdf-profile-download .plate {
  isolation: auto !important;
}
.pdf-profile-download .plate.has-art::before {
  display: none !important;
}
.pdf-profile-download .plate.has-art .plate-label,
.pdf-profile-download .plate.has-art .plate-notes {
  background: var(--paper) !important;
}
.pdf-profile-download .mode-card-panel img {
  opacity: 0.18 !important;
}
""".strip()
    return f"{css}\n{download_css}"


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


def logo_mark(mark_uri: str, class_name: str) -> str:
    if not mark_uri:
        return ""
    return f'<img class="{class_name}" src="{html.escape(mark_uri, quote=True)}" alt="">'


def front_matter_pages(manifest: dict[str, Any], design: dict[str, Any], mark_uri: str) -> list[str]:
    pages: list[str] = []
    front_matter = manifest.get("front_matter") or {}
    note_words = int(design.get("layout", {}).get("front_note_excerpt_words", 150))
    for key in ["creator_note", "machines_note", "editor_letter"]:
        config = front_matter.get(key)
        if not isinstance(config, dict):
            continue
        source = Path(str(config.get("source", "")))
        title, status, body = load_front_note(source, str(config.get("title", key.replace("_", " ").title())))
        rail = key.replace("_note", "").replace("_letter", "").replace("_", " ").upper()
        code = {"creator_note": "HUM", "machines_note": "MAC", "editor_letter": "EDT"}.get(key, "FRT")
        note_class = key.replace("_note", "").replace("_letter", "").replace("_", "-")
        pages.extend(
            [
                f'<section class="page front-note front-note-{html.escape(note_class)}">',
                logo_mark(mark_uri, "front-mark"),
                f'<div class="note-rail"><strong>{html.escape(rail)}</strong><span>{html.escape(code)}</span></div>',
                f'<div class="note-kicker">{html.escape(status)} / front matter</div>',
                f"<h2>{html.escape(title)}</h2>",
                f'<div class="note-body">{excerpt_paragraphs_html(body, note_words)}</div>',
                f'<div class="note-register"><span>{html.escape(code)} / {html.escape(status)}</span><span>{html.escape(source.name)}</span><span>front signal</span></div>',
                folio(key.replace("_", " "), "front"),
                "</section>",
            ]
        )
    return pages


def mode_counts(entries: list[book_build.BookEntry]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for entry in entries:
        counts[entry.layout_mode] = counts.get(entry.layout_mode, 0) + 1
    return counts


def mode_bytes(entries: list[book_build.BookEntry], mode: str) -> str:
    return " ".join(entry.byte_index for entry in entries if entry.layout_mode == mode)


def section_color_spectrum(design: dict[str, Any], palette_name: str) -> str:
    section_palettes = design.get("section_palettes") or {}
    palettes = section_palettes.get(palette_name) if isinstance(section_palettes, dict) else {}
    codes: list[str] = [str(code) for code in palettes.keys()] if isinstance(palettes, dict) else []
    if not codes:
        codes = [format(index, "X") for index in range(16)]
    chips = []
    for code in codes:
        colors = palettes.get(code) if isinstance(palettes, dict) else {}
        if not isinstance(colors, dict):
            colors = {}
        style = "; ".join(
            [
                f"--s-accent: {html.escape(str(colors.get('accent', '#b64127')), quote=True)}",
                f"--s-accent-2: {html.escape(str(colors.get('accent_2', '#145f68')), quote=True)}",
                f"--s-accent-3: {html.escape(str(colors.get('accent_3', '#9b7a24')), quote=True)}",
                f"--s-wash: {html.escape(str(colors.get('wash', '#ebe4d4')), quote=True)}",
            ]
        )
        chips.append(f'<span data-code="{html.escape(code)}" style="{style}"></span>')
    return '<div class="taxonomy-spectrum">' + "".join(chips) + "</div>"


def generation_ref_grid(entries: list[book_build.BookEntry]) -> str:
    cells: list[str] = []
    for entry in entries:
        ref = entry.bit.generation_ref[:7] if entry.bit.generation_ref else "unknown"
        cells.append(f'<span><strong>{html.escape(entry.byte_index)}</strong> gen:{html.escape(ref)}</span>')
    return '<div class="lineage-grid">' + "".join(cells) + "</div>"


def byte_field(entries: list[book_build.BookEntry]) -> str:
    present = {entry.byte_index for entry in entries}
    cells = []
    for index in range(256):
        byte = f"{index:02X}"
        filled = " filled" if byte in present else ""
        cells.append(f'<span class="{filled.strip()}">{byte}</span>')
    return '<div class="byte-field">' + "".join(cells) + "</div>"


def endpaper_page(entries: list[book_build.BookEntry], label: str, side: str) -> str:
    return "\n".join(
        [
            f'<section class="page endpaper endpaper-{html.escape(side)}">',
            f'<div class="endpaper-label">{html.escape(label)}</div>',
            byte_field(entries),
            folio(label.lower(), side),
            "</section>",
        ]
    )


def designed_object_pages(
    entries: list[book_build.BookEntry],
    manifest: dict[str, Any],
    design: dict[str, Any],
    art_direction: dict[str, Any],
    mark_uri: str,
    palette_name: str,
    assets: AssetResolver | None = None,
) -> list[str]:
    counts = mode_counts(entries)
    modes = design.get("layout_modes") or {}
    mode_cards: list[str] = []
    mode_thread: list[str] = []
    for mode, data in modes.items():
        info = mode_info(design, str(mode))
        direction = mode_art_direction(str(mode), art_direction)
        treatment = direction.get("treatment", str(mode))
        panel_uri = mode_panel_uri(design, str(mode), assets)
        panel_img = f'<img src="{html.escape(panel_uri, quote=True)}" alt="">' if panel_uri else ""
        mode_thread.append(
            f'<span class="{mode_css_class(str(mode))}"><strong>{html.escape(info["glyph"])} / {counts.get(str(mode), 0):02d}</strong></span>'
        )
        mode_cards.append(
            "\n".join(
                [
                    f'<div class="mode-card {mode_css_class(str(mode))}">',
                    f'<div class="mode-card-panel">{panel_img}</div>',
                    '<div class="mode-card-body">',
                    f'<div class="mode-card-glyph">{html.escape(info["glyph"])}</div>',
                    f'<h3>{html.escape(info["label"])}</h3>',
                    f'<p>{html.escape(info["description"])}</p>',
                    '<div class="mode-card-colors"><span style="--chip: var(--mode-accent)"></span><span style="--chip: var(--mode-accent-2)"></span><span style="--chip: var(--mode-accent-3)"></span><span style="--chip: var(--mode-wash)"></span></div>',
                    f'<div class="mode-card-meta">{counts.get(str(mode), 0):02d} entries / {html.escape(str(treatment))}</div>',
                    "</div>",
                    "</div>",
                ]
            )
        )
    return [
        "\n".join(
            [
                '<section class="page object-page how-read">',
                logo_mark(mark_uri, "front-mark"),
                '<div class="object-kicker">field key / object 00</div>',
                "<h2>How To Read This Object</h2>",
                '<div class="reading-oracle">',
                '<div class="reading-sigil">00<br>01<br>10<br>11</div>',
                '<div class="reading-body">',
                '<p class="reading-lede">Do not begin at the beginning unless the beginning is already looking back at you.</p>',
                '<p>Each bit is a small locked room with its own weather. Some rooms answer to signal, some to paperwork, some to bone, some to the hum beneath a machine that has not yet admitted it is alive.</p>',
                '<div class="reading-hints">',
                '<div><strong>Address</strong><span>The byte is the handle. Return to it when the page starts pretending to be linear.</span></div>',
                '<div><strong>Signal</strong><span>Static is not absence. It is the archive deciding how much of itself to reveal.</span></div>',
                '<div><strong>Source</strong><span>The generation mark is a fingerprint left by the instrument, not a signature from the ghost.</span></div>',
                '<div><strong>Door</strong><span>The square mark points outward. Use it only after the paper has finished speaking.</span></div>',
                "</div>",
                "</div>",
                "</div>",
                '<div class="reading-register"><span>signal</span><span>ledger</span><span>field</span><span>rule</span><span>myth</span><span>glitch</span><span>door</span><span>checksum</span></div>',
                '<div class="artifact-band"><span>00-FF</span><span>finite / addressable / partially awake</span><span>read by recurrence</span></div>',
                folio("how to read", "front"),
                "</section>",
            ]
        ),
        "\n".join(
            [
                '<section class="page object-page taxonomy-page">',
                '<div class="object-kicker">visual taxonomy / six house styles</div>',
                "<h2>The Six Reading Modes</h2>",
                section_color_spectrum(design, palette_name),
                '<div class="taxonomy-grid">',
                *mode_cards,
                "</div>",
                '<div class="taxonomy-thread">',
                *mode_thread,
                "</div>",
                folio("taxonomy", "front"),
                "</section>",
            ]
        ),
    ]


def back_matter_pages(
    entries: list[book_build.BookEntry],
    manifest: dict[str, Any],
    design: dict[str, Any],
    art_direction: dict[str, Any],
    palette_name: str,
) -> list[str]:
    modes = design.get("layout_modes") or {}
    mode_rows: list[str] = []
    for mode in modes:
        info = mode_info(design, str(mode))
        direction = mode_art_direction(str(mode), art_direction)
        mode_rows.append(
            f'<div><strong>{html.escape(info["glyph"])} / {html.escape(info["label"])}</strong>'
            f'<span>{html.escape(mode_bytes(entries, str(mode)))}</span>'
            f'<em>{html.escape(str(direction.get("treatment", str(mode))))}</em></div>'
        )

    section_rows = []
    for section in manifest.get("sections") or []:
        if not isinstance(section, dict):
            continue
        code = str(section.get("code", "")).upper()
        section_rows.append(
            f'<div><strong>{html.escape(code)}x / {html.escape(str(section.get("title", "")))}</strong>'
            f'<span>{" ".join(html.escape(tag) for tag in section_tags(manifest, code))}</span></div>'
        )

    stories = art_direction.get("stories") or {}
    story_rows = []
    priority_totals = {"hero": 0, "high": 0, "medium": 0, "standard": 0}
    priority_rows = []
    for entry in entries:
        story = art_direction_for(entry, art_direction)
        priority = book_build.art_priority(entry, story)
        if priority not in priority_totals:
            priority = "standard"
        priority_totals[priority] += 1
        if priority in {"hero", "high"}:
            treatment = story.get("treatment") or mode_art_direction(entry.layout_mode, art_direction).get("treatment")
            priority_rows.append(
                f'<div><strong>{html.escape(entry.byte_index)} / {html.escape(entry.bit.title)}</strong>'
                f'<span>{html.escape(priority)} / {html.escape(str(treatment or entry.layout_mode))}</span></div>'
            )
    for key, data in list(stories.items())[:44]:
        if not isinstance(data, dict):
            continue
        story_rows.append(
            f'<div><strong>{html.escape(str(key))}</strong><span>{html.escape(str(data.get("priority", "standard")))} / {html.escape(str(data.get("layout_intent", data.get("treatment", "art direction"))))}</span></div>'
        )
    priority_summary = "".join(
        f"<span>{html.escape(label)}: {priority_totals[label]}</span>" for label in ("hero", "high", "medium", "standard")
    )

    return [
        "\n".join(
            [
                '<section class="page object-page index-page">',
                '<div class="object-kicker">index / by reading mode</div>',
                "<h2>Mode Index</h2>",
                '<div class="mode-index">',
                *mode_rows,
                "</div>",
                folio("mode index", palette_name),
                "</section>",
            ]
        ),
        "\n".join(
            [
                '<section class="page object-page index-page">',
                '<div class="object-kicker">index / by section signal</div>',
                "<h2>Theme Index</h2>",
                '<div class="section-index">',
                *section_rows,
                "</div>",
                folio("theme index", palette_name),
                "</section>",
            ]
        ),
        "\n".join(
            [
                '<section class="page object-page lineage-page">',
                '<div class="object-kicker">generation lineage / obscure reference layer</div>',
                "<h2>Generation Map</h2>",
                generation_ref_grid(entries),
                folio("generation lineage", palette_name),
                "</section>",
            ]
        ),
        "\n".join(
            [
                '<section class="page object-page index-page">',
                '<div class="object-kicker">art registry / production direction</div>',
                "<h2>Art Direction Register</h2>",
                '<div class="section-index compact">',
                *story_rows,
                "</div>",
                folio("art registry", palette_name),
                "</section>",
            ]
        ),
        "\n".join(
            [
                '<section class="page object-page index-page">',
                '<div class="object-kicker">art queue / manual first pass</div>',
                "<h2>Priority Art Plates</h2>",
                f'<div class="artifact-band">{priority_summary}</div>',
                '<div class="section-index compact">',
                *priority_rows[:28],
                "</div>",
                folio("art queue", palette_name),
                "</section>",
            ]
        ),
        "\n".join(
            [
                '<section class="page object-page colophon-page">',
                '<div class="object-kicker">colophon / provenance statement</div>',
                "<h2>Colophon</h2>",
                '<div class="object-columns">',
                f'<p><strong>{html.escape(str(manifest.get("title", "256 Bits")))}</strong> is rendered from the local Obscure Bit archive as a review edition for editorial, art, rights, and production evaluation.</p>',
                '<p>The final commercial edition should include approved artwork, stable QR targets, duplicate/name collision checks, source review, and an explicit rights record for every generated or human-made visual.</p>',
                "</div>",
                '<div class="artifact-band"><span>fonts: cormorant / source serif / space grotesk / ibm plex mono</span><span>palettes: light + dark</span><span>format: print + screen</span></div>',
                folio("colophon", palette_name),
                "</section>",
            ]
        ),
    ]


def certificate_page(
    entries: list[book_build.BookEntry],
    manifest: dict[str, Any],
    art_direction: dict[str, Any],
    palette_name: str,
) -> str:
    stories = art_direction.get("stories") or {}
    priority_count = sum(
        1
        for data in stories.values()
        if isinstance(data, dict) and str(data.get("priority", "")).lower() in {"hero", "high"}
    )
    target = int(manifest.get("target_entry_count", 256))
    fields = [
        ("edition", str(manifest.get("edition_name", "review edition"))),
        ("theme", palette_name),
        ("selected", f"{len(entries):03d} / {target:03d}"),
        ("priority art plates", f"{priority_count:02d} named"),
        ("rights state", "review pending"),
        ("validation state", "draft pass"),
    ]
    rows = "".join(
        f'<div><span>{html.escape(label)}</span><strong>{html.escape(value)}</strong></div>' for label, value in fields
    )
    return "\n".join(
        [
            '<section class="page object-page certificate-page">',
            '<div class="object-kicker">certificate / review artifact</div>',
            "<h2>Certificate Of Assembly</h2>",
            '<div class="certificate-grid">',
            rows,
            "</div>",
            '<div class="certificate-seal">OB<br>256</div>',
            '<p class="packet-note">This certificate becomes final only after all 256 entries, QR targets, art rights, and validation reports are approved for release.</p>',
            folio("certificate", palette_name),
            "</section>",
        ]
    )


def back_cover_page(
    entries: list[book_build.BookEntry],
    manifest: dict[str, Any],
    palette_name: str,
    mark_uri: str,
) -> str:
    selected = len(entries)
    target = int(manifest.get("target_entry_count", 256))
    return "\n".join(
        [
            '<section class="page back-cover">',
            '<div class="back-cover-top">',
            logo_mark(mark_uri, "brand-mark"),
            '<div class="back-cover-code">00-FF / FIRST BYTE / OBSCUREBIT.COM</div>',
            "</div>",
            "<div>",
            f'<h2>{html.escape(str(manifest.get("title", "256 Bits")))}</h2>',
            '<p class="back-cover-blurb">A cabinet of strange signals, forms, myths, ledgers, field notes, and system errors. Each bit is a small addressable artifact; together they make a byte-sized map of the archive.</p>',
            "</div>",
            '<div class="back-cover-meta">',
            f'<span>{selected:03d}/{target:03d} review entries</span>',
            f'<span>{html.escape(str(manifest.get("edition_name", "Review Edition")))}</span>',
            f'<span>{html.escape(palette_name)} edition</span>',
            "</div>",
            '<div class="spine-code">256 BITS / VOLUME 01 / THE FIRST BYTE</div>',
            "</section>",
        ]
    )


def entry_caption_lines(
    entry: book_build.BookEntry,
    continuation: str = "",
) -> list[str]:
    gen_label = generation_label(entry)
    source_label = gen_label or "gen:unrecorded"
    suffix = f" / {continuation}" if continuation else ""
    return [
        f'<span class="caption-accent">bits.obscurebit.com / bit {html.escape(entry.byte_index)}</span>{html.escape(suffix)}',
        f'<span class="caption-state">{html.escape(source_label)}</span> / rights record pending',
    ]


QR_VERSION = 6
QR_SIZE = QR_VERSION * 4 + 17
QR_DATA_CODEWORDS = 108
QR_ECC_CODEWORDS_PER_BLOCK = 16
QR_BLOCK_COUNT = 4
QR_REMAINDER_BITS = 7
QR_ALIGNMENT_POSITIONS = (6, 34)


def _qr_gf_mul(x: int, y: int) -> int:
    result = 0
    while y:
        if y & 1:
            result ^= x
        x <<= 1
        if x & 0x100:
            x ^= 0x11D
        y >>= 1
    return result


def _qr_rs_generator(degree: int) -> list[int]:
    result = [1]
    root = 1
    for _ in range(degree):
        result.append(0)
        for index in range(len(result) - 1):
            result[index] = _qr_gf_mul(result[index], root) ^ result[index + 1]
        result[-1] = _qr_gf_mul(result[-1], root)
        root = _qr_gf_mul(root, 2)
    return result


def _qr_rs_remainder(data: list[int], generator: list[int]) -> list[int]:
    result = [0] * (len(generator) - 1)
    for byte in data:
        factor = byte ^ result.pop(0)
        result.append(0)
        for index, coefficient in enumerate(generator[1:]):
            result[index] ^= _qr_gf_mul(coefficient, factor)
    return result


def _append_bits(bits: list[int], value: int, width: int) -> None:
    for shift in range(width - 1, -1, -1):
        bits.append((value >> shift) & 1)


def _qr_codewords_for_url(url: str) -> list[int]:
    data = url.encode("utf-8")
    max_bytes = QR_DATA_CODEWORDS - 2
    if len(data) > max_bytes:
        raise ValueError(f"QR target is too long for the fixed book QR code: {url}")
    bits: list[int] = []
    _append_bits(bits, 0b0100, 4)
    _append_bits(bits, len(data), 8)
    for byte in data:
        _append_bits(bits, byte, 8)
    _append_bits(bits, 0, min(4, QR_DATA_CODEWORDS * 8 - len(bits)))
    while len(bits) % 8:
        bits.append(0)
    pad = 0xEC
    while len(bits) < QR_DATA_CODEWORDS * 8:
        _append_bits(bits, pad, 8)
        pad = 0x11 if pad == 0xEC else 0xEC
    data_codewords = [sum(bits[index + bit] << (7 - bit) for bit in range(8)) for index in range(0, len(bits), 8)]
    generator = _qr_rs_generator(QR_ECC_CODEWORDS_PER_BLOCK)
    data_blocks = [
        data_codewords[index * (QR_DATA_CODEWORDS // QR_BLOCK_COUNT) : (index + 1) * (QR_DATA_CODEWORDS // QR_BLOCK_COUNT)]
        for index in range(QR_BLOCK_COUNT)
    ]
    ecc_blocks = [_qr_rs_remainder(block, generator) for block in data_blocks]
    result: list[int] = []
    for index in range(QR_DATA_CODEWORDS // QR_BLOCK_COUNT):
        result.extend(block[index] for block in data_blocks)
    for index in range(QR_ECC_CODEWORDS_PER_BLOCK):
        result.extend(block[index] for block in ecc_blocks)
    return result


def _qr_blank_matrix() -> tuple[list[list[bool]], list[list[bool]]]:
    modules = [[False] * QR_SIZE for _ in range(QR_SIZE)]
    reserved = [[False] * QR_SIZE for _ in range(QR_SIZE)]

    def set_function(x: int, y: int, dark: bool) -> None:
        if 0 <= x < QR_SIZE and 0 <= y < QR_SIZE:
            modules[y][x] = dark
            reserved[y][x] = True

    def draw_finder(x: int, y: int) -> None:
        for dy in range(-1, 8):
            for dx in range(-1, 8):
                xx = x + dx
                yy = y + dy
                dark = (
                    0 <= dx <= 6
                    and 0 <= dy <= 6
                    and (dx in (0, 6) or dy in (0, 6) or (2 <= dx <= 4 and 2 <= dy <= 4))
                )
                set_function(xx, yy, dark)

    def draw_alignment(cx: int, cy: int) -> None:
        for dy in range(-2, 3):
            for dx in range(-2, 3):
                set_function(cx + dx, cy + dy, max(abs(dx), abs(dy)) != 1)

    draw_finder(0, 0)
    draw_finder(QR_SIZE - 7, 0)
    draw_finder(0, QR_SIZE - 7)
    for i in range(8, QR_SIZE - 8):
        set_function(i, 6, i % 2 == 0)
        set_function(6, i, i % 2 == 0)
    draw_alignment(QR_ALIGNMENT_POSITIONS[1], QR_ALIGNMENT_POSITIONS[1])
    set_function(8, QR_VERSION * 4 + 9, True)
    _qr_draw_format_bits(modules, reserved, 0, reserve_only=True)
    return modules, reserved


def _qr_format_bits(mask: int) -> int:
    data = mask  # ECC level M is 00 in QR format bits.
    bits = data << 10
    generator = 0x537
    for shift in range(14, 9, -1):
        if (bits >> shift) & 1:
            bits ^= generator << (shift - 10)
    return ((data << 10) | bits) ^ 0x5412


def _qr_draw_format_bits(
    modules: list[list[bool]],
    reserved: list[list[bool]] | None,
    mask: int,
    reserve_only: bool = False,
) -> None:
    bits = _qr_format_bits(mask)

    def draw(x: int, y: int, index: int) -> None:
        if reserve_only and reserved is not None:
            reserved[y][x] = True
            return
        modules[y][x] = ((bits >> index) & 1) != 0
        if reserved is not None:
            reserved[y][x] = True

    for i in range(6):
        draw(8, i, i)
    draw(8, 7, 6)
    draw(8, 8, 7)
    draw(7, 8, 8)
    for i in range(9, 15):
        draw(14 - i, 8, i)
    for i in range(8):
        draw(QR_SIZE - 1 - i, 8, i)
    for i in range(8, 15):
        draw(8, QR_SIZE - 15 + i, i)
    modules[QR_VERSION * 4 + 9][8] = True


def _qr_mask_bit(mask: int, x: int, y: int) -> bool:
    if mask == 0:
        return (x + y) % 2 == 0
    if mask == 1:
        return y % 2 == 0
    if mask == 2:
        return x % 3 == 0
    if mask == 3:
        return (x + y) % 3 == 0
    if mask == 4:
        return (y // 2 + x // 3) % 2 == 0
    if mask == 5:
        return (x * y) % 2 + (x * y) % 3 == 0
    if mask == 6:
        return ((x * y) % 2 + (x * y) % 3) % 2 == 0
    return ((x + y) % 2 + (x * y) % 3) % 2 == 0


def _qr_apply_data(modules: list[list[bool]], reserved: list[list[bool]], codewords: list[int]) -> None:
    bits: list[int] = []
    for byte in codewords:
        _append_bits(bits, byte, 8)
    bits.extend([0] * QR_REMAINDER_BITS)
    bit_index = 0
    upward = True
    x = QR_SIZE - 1
    while x > 0:
        if x == 6:
            x -= 1
        y_range = range(QR_SIZE - 1, -1, -1) if upward else range(QR_SIZE)
        for y in y_range:
            for dx in range(2):
                xx = x - dx
                if not reserved[y][xx]:
                    modules[y][xx] = bit_index < len(bits) and bits[bit_index] == 1
                    bit_index += 1
        upward = not upward
        x -= 2


def _qr_penalty(modules: list[list[bool]]) -> int:
    penalty = 0
    for horizontal in (True, False):
        for outer in range(QR_SIZE):
            run_color = False
            run_len = 0
            for inner in range(QR_SIZE):
                color = modules[outer][inner] if horizontal else modules[inner][outer]
                if inner == 0 or color != run_color:
                    if run_len >= 5:
                        penalty += 3 + run_len - 5
                    run_color = color
                    run_len = 1
                else:
                    run_len += 1
            if run_len >= 5:
                penalty += 3 + run_len - 5
    for y in range(QR_SIZE - 1):
        for x in range(QR_SIZE - 1):
            color = modules[y][x]
            if modules[y][x + 1] == color and modules[y + 1][x] == color and modules[y + 1][x + 1] == color:
                penalty += 3
    pattern = (True, False, True, True, True, False, True)
    for horizontal in (True, False):
        for outer in range(QR_SIZE):
            line = [modules[outer][i] if horizontal else modules[i][outer] for i in range(QR_SIZE)]
            for index in range(QR_SIZE - 6):
                if tuple(line[index : index + 7]) == pattern:
                    before = index >= 4 and not any(line[index - 4 : index])
                    after = index + 11 <= QR_SIZE and not any(line[index + 7 : index + 11])
                    if before or after:
                        penalty += 40
    dark = sum(1 for row in modules for value in row if value)
    total = QR_SIZE * QR_SIZE
    penalty += abs(dark * 20 - total * 10) // total * 10
    return penalty


def qr_matrix(url: str) -> list[list[bool]]:
    codewords = _qr_codewords_for_url(url)
    base, reserved = _qr_blank_matrix()
    _qr_apply_data(base, reserved, codewords)
    best: list[list[bool]] | None = None
    best_penalty: int | None = None
    for mask in range(8):
        candidate = [row[:] for row in base]
        for y in range(QR_SIZE):
            for x in range(QR_SIZE):
                if not reserved[y][x] and _qr_mask_bit(mask, x, y):
                    candidate[y][x] = not candidate[y][x]
        _qr_draw_format_bits(candidate, None, mask)
        penalty = _qr_penalty(candidate)
        if best_penalty is None or penalty < best_penalty:
            best = candidate
            best_penalty = penalty
    assert best is not None
    return best


def qr_svg(url: str, label: str) -> str:
    qr = segno.make(url, error="m", micro=False, boost_error=False)
    matrix = qr.matrix
    quiet = 4
    size = len(matrix) + quiet * 2
    rects = []
    for y, row in enumerate(matrix):
        start: int | None = None
        for x, dark in enumerate(list(row) + [0]):
            if dark and start is None:
                start = x
            elif not dark and start is not None:
                rects.append(f"M{start + quiet} {y + quiet}h{x - start}v1h-{x - start}z")
                start = None
    return (
        f'<svg class="qr-code" viewBox="0 0 {size} {size}" role="img" '
        f'aria-label="{html.escape(label, quote=True)}" xmlns="http://www.w3.org/2000/svg">'
        f"<title>{html.escape(label)}</title>"
        f'<rect width="{size}" height="{size}" fill="#fff"/>'
        f'<path class="qr-modules" d="{" ".join(rects)}"/></svg>'
    )


def entry_head_html(
    entry: book_build.BookEntry,
    manifest: dict[str, Any],
    design: dict[str, Any],
    heading_suffix: str = "",
) -> str:
    mode = mode_info(design, entry.layout_mode)
    gen_label = generation_label(entry)
    gen_meta = f' / <span class="gen-ref">{html.escape(gen_label)}</span>' if gen_label else ""
    heading = html.escape(entry.bit.title)
    if heading_suffix:
        heading += f" <span>{html.escape(heading_suffix)}</span>"
    return "\n".join(
        [
            '<div class="entry-head">',
            f'<div class="byte">{entry.byte_index}</div>',
            "<div>",
            f"<h3>{heading}</h3>",
            f'<div class="meta">{html.escape(entry.bit.date)} / {html.escape(entry.bit.theme or "unmarked")} / {html.escape(section_title(manifest, entry.section_code))}{gen_meta}</div>',
            f'<div class="mode-badge">{html.escape(mode["glyph"])} / {html.escape(mode["label"])}</div>',
            "</div>",
            "</div>",
        ]
    )


def plate_html(
    entry: book_build.BookEntry,
    design: dict[str, Any],
    art_direction: dict[str, Any] | None = None,
    role: str = "opener",
    art_index: int = 0,
    allow_art_fallback: bool = True,
    assets: AssetResolver | None = None,
) -> str:
    art_direction = art_direction or {}
    identity = plate_identity(entry, design)
    story_direction = art_direction_for(entry, art_direction)
    mode_direction = mode_art_direction(entry.layout_mode, art_direction)
    treatment = art_treatment_for(entry, art_direction)
    material = str(story_direction.get("material") or mode_direction.get("material") or "source plate")
    gesture = str(story_direction.get("gesture") or mode_direction.get("gesture") or "registered plate")
    asset_uri = art_asset_uri_for(entry, art_direction, role, art_index, allow_art_fallback, assets)
    art_extra = art_classes_for(entry, art_direction, role, art_index, allow_art_fallback)
    art_class = (" has-art " + art_extra).rstrip() if asset_uri else ""
    art_style = art_style_for(entry, art_direction, role, art_index, allow_art_fallback)
    style_attr = f' style="{art_style}"' if art_style else ""
    art_image = f'<img class="plate-art-img" src="{asset_uri}" alt="">' if asset_uri else ""
    return "\n".join(
        [
            f'<div class="plate plate-{html.escape(treatment)}{art_class}"{style_attr}>',
            art_image,
            '<div class="plate-grid"><span></span><span></span><span></span><span></span></div>',
            '<div class="plate-thread"></div>',
            '<div class="plate-orbit"></div>',
            '<div class="plate-dots"></div>',
            '<div class="plate-notch plate-notch-a"></div>',
            '<div class="plate-notch plate-notch-b"></div>',
            f'<div class="plate-sigil">{html.escape(identity["glyph"])}</div>',
            f'<div class="plate-label">{html.escape(identity["label"])} Plate</div>',
            f'<div class="plate-notes">{html.escape(material)} / {html.escape(gesture)}</div>',
            "</div>",
        ]
    )


def entry_foot_html(entry: book_build.BookEntry, page_num: int, continuation: str = "") -> str:
    caption_lines = entry_caption_lines(entry, continuation)
    qr_label = f"QR code for Bit {entry.byte_index}: {entry.qr_target}"
    qr_markup = qr_svg(entry.qr_target, qr_label)
    return "\n".join(
        [
            '<div class="entry-foot">',
            f'<div class="caption">{"<br>".join(caption_lines)}</div>',
            f'<a class="qr" href="{html.escape(entry.qr_target, quote=True)}">{qr_markup}</a>',
            "</div>",
            folio(entry.byte_index, str(page_num)),
        ]
    )


def render_standard_entry_page(
    entry: book_build.BookEntry,
    manifest: dict[str, Any],
    design: dict[str, Any],
    art_direction: dict[str, Any],
    page_num: int,
    assets: AssetResolver,
) -> str:
    body = render_story_blocks(story_blocks(entry.bit.body, body_word_count(entry.bit.body), include_dividers=True))
    quote = pull_quote(entry.bit.body)
    parts = [
        section_open_tag(entry, design),
        entry_head_html(entry, manifest, design),
        plate_html(entry, design, art_direction, assets=assets),
    ]
    if teaser_enabled(entry, design):
        parts.append(f'<div class="entry-pullquote">{html.escape(quote)}</div>')
    parts.extend(
        [
            f'<div class="entry-body">{body}</div>',
            entry_foot_html(entry, page_num),
            "</section>",
        ]
    )
    return "\n".join(parts)


def render_spread_entry_pages(
    entry: book_build.BookEntry,
    manifest: dict[str, Any],
    design: dict[str, Any],
    art_direction: dict[str, Any],
    first_page_num: int,
    assets: AssetResolver,
) -> list[str]:
    pages = split_story_for_pages(entry, design)
    quote = pull_quote(entry.bit.body)

    open_parts = [
        section_open_tag(entry, design, "spread-open"),
        entry_head_html(entry, manifest, design),
        plate_html(entry, design, art_direction, assets=assets),
    ]
    if teaser_enabled(entry, design):
        open_parts.append(f'<div class="entry-pullquote">{html.escape(quote)}</div>')
    open_parts.extend(
        [
            f'<div class="entry-body">{render_story_blocks(pages[0])}</div>',
            entry_foot_html(entry, first_page_num, "continues next page" if len(pages) > 1 else ""),
            "</section>",
        ]
    )
    open_page = "\n".join(open_parts)
    rendered_pages = [open_page]
    for index, page_blocks in enumerate(pages[1:], start=1):
        page_num = first_page_num + index
        continuation = "continues next page" if index < len(pages) - 1 else ""
        override = story_layout_override(entry, design)
        tail_plate_words = int(
            override.get("tail_plate_max_words")
            or design.get("layout", {}).get("tail_plate_max_words", 190)
        )
        is_tail_continuation = (
            (entry.layout_mode != "glitch" or bool(override.get("allow_glitch_tail_plate")))
            and index == len(pages) - 1
            and page_word_count(page_blocks) < tail_plate_words
        )
        tail_words = page_word_count(page_blocks)
        tail_density = " tail-compact" if tail_words < 170 else " tail-balanced"
        tail_class = f" tail-continuation{tail_density}" if is_tail_continuation else ""
        continuation_has_art = (not is_tail_continuation) and has_explicit_art_variant_for(entry, art_direction, "continuation", index)
        continuation_class = " art-continuation" if continuation_has_art else ""
        tail_plate = plate_html(entry, design, art_direction, "tail", index, assets=assets) if is_tail_continuation else ""
        continuation_plate = (
            plate_html(entry, design, art_direction, "continuation", index, allow_art_fallback=False, assets=assets)
            if continuation_has_art
            else ""
        )
        rendered_pages.append(
            "\n".join(
                [
                    section_open_tag(entry, design, f"spread-text{tail_class}{continuation_class}"),
                    entry_head_html(entry, manifest, design, "continued"),
                    continuation_plate,
                    f'<div class="entry-body">{render_story_blocks(page_blocks)}</div>',
                    tail_plate,
                    entry_foot_html(entry, page_num, continuation),
                    "</section>",
                ]
            )
        )
    return rendered_pages


def render_html(
    entries: list[book_build.BookEntry],
    warnings: list[str],
    manifest: dict[str, Any],
    design: dict[str, Any],
    art_direction: dict[str, Any],
    palette_name: str,
    assets: AssetResolver,
    mark_uri: str = "",
) -> str:
    palette = design["palettes"][palette_name]
    cover_uri = cover_art_uri(design, assets)
    parts = [
        "<!doctype html>",
        "<html>",
        "<head>",
        '<meta charset="utf-8">',
        f"<title>{html.escape(str(manifest.get('title', '256 Bits')))} - {palette_name}</title>",
        "<style>",
        css_for(design, palette_name),
        pdf_profile_css(assets.profile_name),
        "</style>",
        "</head>",
        f'<body class="pdf-profile-{html.escape(assets.profile_name)}">',
        '<section class="page cover">',
        f'<img class="cover-art" src="{html.escape(cover_uri, quote=True)}" alt="">' if cover_uri else "",
        '<div class="cover-top">',
        '<div class="cover-code">00 01 02 03 04 05 06 07<br>08 09 0A 0B 0C 0D 0E 0F<br><br>10 11 12 13 14 15 16 17<br>18 19 1A 1B 1C 1D 1E 1F</div>',
        logo_mark(mark_uri, "brand-mark"),
        "</div>",
        "<div>",
        f"<h1>{html.escape(str(manifest.get('title', '256 Bits')))}</h1>",
        f'<div class="subtitle">{html.escape(str(manifest.get("subtitle", "Volume 1")))}</div>',
        f'<div class="edition">{html.escape(str(manifest.get("edition_name", "Review Edition")))} / {html.escape(palette["name"])}</div>',
        "</div>",
        "</section>",
        endpaper_page(entries, "Front Endpaper / 00-FF", "front"),
        *front_matter_pages(manifest, design, mark_uri),
        *designed_object_pages(entries, manifest, design, art_direction, mark_uri, palette_name, assets),
        '<section class="page toc">',
        '<div class="object-kicker">address field / occupied and dark bytes</div>',
        "<h2>Memory Map</h2>",
        contents_register(entries),
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
            section_panel_uri_value = section_panel_uri(design, current_section, assets)
            section_panel = (
                f'<div class="section-panel"><img src="{html.escape(section_panel_uri_value, quote=True)}" alt=""></div>'
                if section_panel_uri_value
                else ""
            )
            parts.extend(
                [
                    f'<section class="page section {section_css_class(current_section)}">',
                    "<div>",
                    f'<div class="section-mark">{html.escape(current_section)}x</div>',
                    f'<div class="section-glyph">{html.escape(section_glyph(design, current_section))} / {html.escape(count_label(len(section_entries(entries, current_section))))}</div>',
                    f"<h2>{html.escape(section_title(manifest, current_section))}</h2>",
                    '<div class="section-tags">'
                    + "".join(f"<span>{html.escape(tag)}</span>" for tag in section_tags(manifest, current_section))
                    + "</div>",
                    section_panel,
                    "</div>",
                    section_strip(entries, current_section),
                    folio(f"section {current_section}", str(page_num)),
                    "</section>",
                ]
            )
        page_num += 1
        if is_spread_entry(entry, design):
            spread_pages = render_spread_entry_pages(entry, manifest, design, art_direction, page_num, assets)
            parts.extend(spread_pages)
            page_num += len(spread_pages) - 1
        else:
            parts.append(render_standard_entry_page(entry, manifest, design, art_direction, page_num, assets))

    parts.extend(back_matter_pages(entries, manifest, design, art_direction, palette_name))
    parts.extend(
        [
            '<section class="page notes">',
            "<h2>Release Notes</h2>",
            f'<p class="muted">This review edition contains {len(entries)} selected entries. It is intentionally incomplete until the volume reaches 256 approved bits.</p>',
            "<ul>",
        ]
    )
    notes_limit = 18
    for warning in warnings[:notes_limit]:
        parts.append(f"<li>{html.escape(warning)}</li>")
    if len(warnings) > notes_limit:
        parts.append(f"<li>{len(warnings) - notes_limit} additional blockers in validation-report.md</li>")
    parts.extend(
        [
            "</ul>",
            folio("validation", "end"),
            "</section>",
            certificate_page(entries, manifest, art_direction, palette_name),
            endpaper_page(entries, "Back Endpaper / Checksum Field", "back"),
            back_cover_page(entries, manifest, palette_name, mark_uri),
            print_fit_script(),
            "</body>",
            "</html>",
        ]
    )
    return "\n".join(parts)


def folio(left: str, right: str) -> str:
    return f'<div class="folio"><span>{html.escape(left)}</span><span>{html.escape(right)}</span></div>'


def print_fit_script() -> str:
    return """
<script>
(() => {
  function fitStoryBodies() {
    document.querySelectorAll(".entry").forEach((entry) => {
      const body = entry.querySelector(".entry-body");
      if (!body) return;

      const computed = window.getComputedStyle(body);
      if (!body.dataset.fitBaseFont) {
        const measuredFont = parseFloat(computed.fontSize) || 10;
        const measuredLine = parseFloat(computed.lineHeight) || measuredFont * 1.45;
        body.dataset.fitBaseFont = measuredFont.toString();
        body.dataset.fitBaseLine = measuredLine.toString();
      }
      const baseFont = parseFloat(body.dataset.fitBaseFont) || 10;
      const baseLine = parseFloat(body.dataset.fitBaseLine) || baseFont * 1.45;
      let scale = 1;

      body.style.fontSize = `${baseFont}px`;
      body.style.lineHeight = `${baseLine}px`;
      body.dataset.fitScale = "1.00";
      entry.classList.remove("fit-shrunk", "fit-overflow-risk");

      const overflows = () =>
        body.scrollHeight > body.clientHeight + 1 ||
        body.scrollWidth > body.clientWidth + 1;

      while (overflows() && scale > 0.7) {
        scale = Math.max(0.7, scale - 0.025);
        body.style.fontSize = `${baseFont * scale}px`;
        body.style.lineHeight = `${baseLine * scale}px`;
        body.dataset.fitScale = scale.toFixed(2);
      }

      if (scale < 0.995) entry.classList.add("fit-shrunk");
      if (overflows()) entry.classList.add("fit-overflow-risk");
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", fitStoryBodies);
  } else {
    fitStoryBodies();
  }
  window.addEventListener("beforeprint", fitStoryBodies);
})();
</script>""".strip()


def chrome_path() -> str | None:
    env_candidates = [
        os.environ.get("CHROME_BIN"),
        os.environ.get("CHROME_PATH"),
    ]
    candidates = [
        *env_candidates,
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
        shutil.which("google-chrome"),
        shutil.which("google-chrome-stable"),
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    return None


def print_pdf(html_path: Path, pdf_path: Path, timeout_seconds: int = 180) -> None:
    chrome = chrome_path()
    if not chrome:
        raise RuntimeError("Chrome/Chromium not found; HTML was rendered but PDF cannot be printed.")
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    if pdf_path.exists():
        pdf_path.unlink()
    with tempfile.TemporaryDirectory(prefix="obscurebit-chrome-profile-") as profile_dir:
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
            "--virtual-time-budget=1000",
            f"--user-data-dir={profile_dir}",
            f"--print-to-pdf={pdf_path.resolve()}",
            html_path.resolve().as_uri(),
        ]
        process = subprocess.Popen(command)
        deadline = time.monotonic() + timeout_seconds
        last_size = -1
        stable_ticks = 0
        while time.monotonic() < deadline:
            return_code = process.poll()
            if pdf_path.exists():
                size = pdf_path.stat().st_size
                if size > 0 and size == last_size:
                    stable_ticks += 1
                else:
                    stable_ticks = 0
                last_size = size
                if stable_ticks >= 2:
                    if return_code is None:
                        process.terminate()
                        try:
                            process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            process.kill()
                    return
            if return_code is not None:
                break
            time.sleep(1)
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
        if not pdf_path.exists() or pdf_path.stat().st_size == 0:
            raise RuntimeError(f"Chrome did not produce PDF output at {pdf_path}")


def render(
    volume_dir: Path,
    output_dir: Path,
    palette_name: str,
    formats: set[str],
    pdf_profile: str,
    asset_mode: str,
) -> tuple[Path, Path | None, list[str]]:
    manifest = book_build.read_yaml(volume_dir / "manifest.yaml")
    design = read_yaml(volume_dir / "design.yaml")
    art_direction = read_optional_yaml(volume_dir / ART_DIRECTION_PATH)
    art_entries = book_build.load_art_entries(volume_dir / "art_manifest.yaml")
    art_direction["_art_entries"] = art_entries
    posts = book_build.discover_bit_posts(editorial_dir=volume_dir / "stories")
    entries, warnings = book_build.build_entries(manifest, posts, art_entries)
    name_blockers, name_warnings = book_build.validate_name_collisions(entries, manifest)
    warnings = book_build.validate_front_matter(manifest) + warnings
    warnings.extend(name_blockers)
    warnings.extend(name_warnings)

    output_dir.mkdir(parents=True, exist_ok=True)
    profile_suffix = str(PDF_IMAGE_PROFILES[pdf_profile]["suffix"])
    html_path = output_dir / f"256-bits-volume-1-{palette_name}-{profile_suffix}.html"
    pdf_path = output_dir / f"256-bits-volume-1-{palette_name}-{profile_suffix}.pdf"
    assets = AssetResolver(volume_dir, output_dir, pdf_profile, asset_mode)
    mark_uri = assets.uri(volume_dir / LOGO_MARK_PATH)
    html_path.write_text(render_html(entries, warnings, manifest, design, art_direction, palette_name, assets, mark_uri))

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
        html_path, pdf_path, warnings = render(
            Path(args.volume_dir),
            Path(args.output_dir),
            theme_name,
            formats,
            args.pdf_profile,
            args.asset_mode,
        )
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
