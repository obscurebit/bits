#!/usr/bin/env python3
"""Build draft production artifacts for the 256 Bits book."""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import yaml


DEFAULT_VOLUME_DIR = Path("book/source/volume-1")
DEFAULT_OUTPUT_DIR = Path("book/output/volume-1")
BIT_POSTS_DIR = Path("docs/bits/posts")
ART_DIRECTION_FILE = "art_direction.yaml"


@dataclass(frozen=True)
class BitPost:
    path: Path
    slug: str
    date: str
    title: str
    description: str
    theme: str
    body: str
    generation_ref: str = ""
    generation_url: str = ""
    web_path: Path | None = None
    book_source_path: Path | None = None
    book_text_edited: bool = False


@dataclass(frozen=True)
class BookEntry:
    byte_index: str
    section_code: str
    bit: BitPost
    qr_target: str
    art_status: str
    art_lane: str
    layout_mode: str
    validation_notes: list[str]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build local book production artifacts")
    parser.add_argument("--volume-dir", default=str(DEFAULT_VOLUME_DIR), help="Directory containing manifest.yaml")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Ignored output directory")
    parser.add_argument(
        "--allow-incomplete",
        action="store_true",
        help="Write draft artifacts even when fewer than 256 selected entries are available",
    )
    return parser.parse_args()


def read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Missing required YAML file: {path}")
    data = yaml.safe_load(path.read_text()) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Expected mapping in {path}")
    return data


def read_optional_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return read_yaml(path)


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---", 4)
    if end == -1:
        return {}, text
    raw = text[4:end].strip()
    body = text[end + 4 :].lstrip()
    frontmatter: dict[str, str] = {}
    for line in raw.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        frontmatter[key.strip()] = value.strip().strip("\"'")
    return frontmatter, body


def clean_title(value: str) -> str:
    title = value.strip()
    while (title.startswith("**") and title.endswith("**")) or (title.startswith("*") and title.endswith("*")):
        if title.startswith("**"):
            title = title[2:-2].strip()
        else:
            title = title[1:-1].strip()
    return title


def strip_site_chrome(body: str) -> str:
    cleaned_lines: list[str] = []
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith('<button class="share-btn"') or stripped.startswith('<div style='):
            break
        cleaned_lines.append(line)
    cleaned_lines = strip_drafting_notes(cleaned_lines)
    cleaned_lines = strip_bold_body_heading(cleaned_lines)
    return "\n".join(cleaned_lines).strip()


def strip_bold_body_heading(lines: list[str]) -> list[str]:
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        match = re.fullmatch(r"(#{1,6})\s+\*\*(.+?)\*\*", stripped)
        if match:
            lines[index] = f"{match.group(1)} {match.group(2).strip()}"
        break
    return lines


def strip_drafting_notes(lines: list[str]) -> list[str]:
    """Remove model/editor drafting notes that occasionally trail generated posts."""
    note_prefixes = (
        "title note:",
        "speculative element:",
        "emotional turn:",
        "ending:",
        "final line",
        "fresh detail:",
        "funny line:",
        "funny sentence:",
        "image:",
        "note:",
        "refusal:",
        "strange image:",
        "stranger image:",
        "the reader may catch",
        "the funny sentence:",
        "this story avoids",
        "unpredicted sentence:",
        "unpredictable sentence:",
    )
    filtered: list[str] = []
    for line in lines:
        normalized = line.strip().lower()
        normalized = re.sub(r"^[\s*_`#>-]+", "", normalized)
        normalized = normalized.replace("**", "").replace("__", "")
        normalized = re.sub(r"[*_`]+", "", normalized)
        normalized = re.sub(r"[\s*_`:-]+$", "", normalized)
        if any(normalized.startswith(prefix) for prefix in note_prefixes):
            continue
        if normalized in {"end", "the end."}:
            continue
        filtered.append(line)
    while filtered and (not filtered[-1].strip() or filtered[-1].strip() == "---"):
        filtered.pop()
    return filtered


def extract_generation_ref(body: str) -> tuple[str, str]:
    ref_match = re.search(r"\bgen:([0-9a-f]{7,40}|unknown)\b", body, re.IGNORECASE)
    if not ref_match:
        return "", ""
    ref = ref_match.group(1).lower()
    url = ""
    link_match = re.search(r'<a\s+[^>]*href="([^"]+)"[^>]*>\s*gen:' + re.escape(ref), body, re.IGNORECASE)
    if link_match:
        url = link_match.group(1)
    elif ref != "unknown":
        url = f"https://github.com/obscurebit/b1ts/tree/{ref}"
    return ref, url


def discover_bit_posts(posts_dir: Path = BIT_POSTS_DIR, editorial_dir: Path | None = None) -> list[BitPost]:
    posts: list[BitPost] = []
    for path in sorted(posts_dir.glob("*.md")):
        text = path.read_text()
        frontmatter, body = parse_frontmatter(text)
        slug = path.stem
        date_value = frontmatter.get("date") or slug[:10]
        title = clean_title(frontmatter.get("title") or title_from_body(body) or slug)
        description = frontmatter.get("description", "")
        theme = frontmatter.get("theme", "")
        generation_ref, generation_url = extract_generation_ref(body)

        source_path = path
        source_body = body
        source_title = title
        book_source_path = None
        book_text_edited = False
        if editorial_dir:
            editorial_path = editorial_dir / path.name
            if editorial_path.exists():
                editorial_frontmatter, editorial_body = parse_frontmatter(editorial_path.read_text())
                source_path = editorial_path
                source_body = editorial_body
                source_title = clean_title(
                    editorial_frontmatter.get("title") or title_from_body(editorial_body) or title
                )
                book_source_path = editorial_path
                book_text_edited = True

        posts.append(
            BitPost(
                path=source_path,
                slug=slug,
                date=date_value,
                title=source_title,
                description=description,
                theme=theme,
                body=strip_site_chrome(source_body),
                generation_ref=generation_ref,
                generation_url=generation_url,
                web_path=path,
                book_source_path=book_source_path,
                book_text_edited=book_text_edited,
            )
        )
    return posts


def title_from_body(body: str) -> str:
    for line in body.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def selected_slugs(manifest: dict[str, Any], posts: Iterable[BitPost]) -> list[str]:
    configured = manifest.get("selected_entries") or []
    if configured:
        slugs: list[str] = []
        for item in configured:
            if isinstance(item, str):
                slugs.append(item)
            elif isinstance(item, dict) and item.get("slug"):
                slugs.append(str(item["slug"]))
        return slugs
    return [post.slug for post in posts]


def has_configured_selection(manifest: dict[str, Any]) -> bool:
    return bool(manifest.get("selected_entries") or [])


def section_codes(manifest: dict[str, Any]) -> list[str]:
    codes: list[str] = []
    for section in manifest.get("sections") or []:
        if isinstance(section, dict) and section.get("code"):
            codes.append(str(section["code"]).upper())
    return codes


def load_art_entries(path: Path) -> dict[str, dict[str, Any]]:
    data = read_yaml(path)
    entries = data.get("entries") or []
    result: dict[str, dict[str, Any]] = {}
    for item in entries:
        if isinstance(item, dict) and item.get("slug"):
            result[str(item["slug"])] = item
    return result


def art_direction_for_entry(entry: BookEntry, art_direction: dict[str, Any]) -> dict[str, Any]:
    mode_defaults = art_direction.get("mode_defaults") or {}
    stories = art_direction.get("stories") or {}
    direction: dict[str, Any] = {}
    if isinstance(mode_defaults.get(entry.layout_mode), dict):
        direction.update(mode_defaults[entry.layout_mode])
    for key in (entry.byte_index, entry.bit.slug, entry.bit.title):
        if isinstance(stories.get(key), dict):
            direction.update(stories[key])
    return direction


def art_priority(entry: BookEntry, direction: dict[str, Any]) -> str:
    explicit = str(direction.get("priority", "")).lower()
    if explicit in {"hero", "high", "medium", "standard"}:
        return explicit
    if entry.byte_index in {"00", "FF"}:
        return "hero"
    if entry.byte_index.endswith("0"):
        return "high"
    if len(entry.bit.body.split()) >= 850:
        return "medium"
    return "standard"


def art_lane_for_priority(priority: str, current_lane: str) -> str:
    if priority in {"hero", "high"}:
        return "manual_hero"
    if current_lane and current_lane != "auto_draft":
        return current_lane
    return "auto_draft"


def art_expected_use(entry: BookEntry, priority: str) -> str:
    if priority == "hero":
        return "cover or full-spread candidate"
    if entry.byte_index.endswith("0"):
        return "section opener and story plate"
    if priority == "high":
        return "full-page or story-defining plate"
    if priority == "medium":
        return "large story plate or paired spot art"
    return "story plate, spot art, or texture source"


def art_seed_text(entry: BookEntry) -> str:
    text = re.sub(r"^# .*$", "", entry.bit.body, flags=re.MULTILINE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= 420:
        return text
    return text[:420].rsplit(" ", 1)[0] + "..."


def art_negative_prompt(forbidden: list[str]) -> str:
    base = [
        "no readable brand logos",
        "no celebrity likeness",
        "no named fictional characters",
        "no franchise references",
        "no living artist style imitation",
        "no copied reference image",
        "no legible copyrighted text",
    ]
    merged = list(dict.fromkeys(base + forbidden))
    return "; ".join(merged)


def art_prompt(entry: BookEntry, direction: dict[str, Any]) -> str:
    treatment = direction.get("treatment", f"{entry.layout_mode.replace('_', ' ')} plate")
    material = direction.get("material", "archival paper, ink, and print texture")
    gesture = direction.get("gesture", "quiet editorial composition with tactile details")
    layout_intent = direction.get("layout_intent", "literary artifact")
    seed = art_seed_text(entry)
    return (
        "Original editorial art for a speculative literary coffee-table book. "
        f"Bit {entry.byte_index}, titled '{entry.bit.title}'. "
        f"Intent: {layout_intent}. Treatment: {treatment}. "
        f"Material language: {material}. Visual gesture: {gesture}. "
        f"Story seed: {seed} "
        "Make it atmospheric, tactile, abstract enough to avoid illustrating protected IP, "
        "with no readable logo or brand marks. Leave generous negative space for book layout."
    )


def art_brief_payload(
    entry: BookEntry,
    art_direction: dict[str, Any],
    art_manifest: dict[str, Any],
) -> dict[str, Any]:
    direction = art_direction_for_entry(entry, art_direction)
    forbidden = list(art_manifest.get("global_forbidden") or [])
    priority = art_priority(entry, direction)
    lane = art_lane_for_priority(priority, entry.art_lane)
    return {
        "byte_index": entry.byte_index,
        "section_code": entry.section_code,
        "slug": entry.bit.slug,
        "title": entry.bit.title,
        "original_date": entry.bit.date,
        "layout_mode": entry.layout_mode,
        "priority": priority,
        "recommended_lane": lane,
        "current_lane": entry.art_lane,
        "status": entry.art_status,
        "expected_use": art_expected_use(entry, priority),
        "aspect_ratio": art_manifest.get("default_aspect_ratio", "4:5"),
        "minimum_output": art_manifest.get("default_output_size", "print-ready source"),
        "treatment": direction.get("treatment", f"{entry.layout_mode.replace('_', ' ')} plate"),
        "material": direction.get("material", "archival paper, ink, and print texture"),
        "gesture": direction.get("gesture", "quiet editorial composition with tactile details"),
        "prompt": art_prompt(entry, direction),
        "negative_prompt": art_negative_prompt(forbidden),
        "review_checklist": [
            "Original composition with no recognizable protected IP.",
            "Works in both light and dark book themes, or has an approved alternate crop.",
            "Has enough quiet space for title, folio, QR label, or margin notes if needed.",
            "Looks intentional as print art, not a generic generated image.",
            "Prompt, provider, generation date, edits, and final approver are recorded.",
        ],
    }


def build_entries(
    manifest: dict[str, Any],
    posts: list[BitPost],
    art_entries: dict[str, dict[str, Any]],
) -> tuple[list[BookEntry], list[str]]:
    target_count = int(manifest.get("target_entry_count", 256))
    canonical_base = str(manifest.get("canonical_url_base", "")).rstrip("/")
    posts_by_slug = {post.slug: post for post in posts}
    warnings: list[str] = []
    entries: list[BookEntry] = []

    if not has_configured_selection(manifest) and section_codes(manifest):
        entries, warnings = build_thematic_entries(manifest, posts, art_entries, canonical_base, target_count)
        if len(entries) < target_count:
            warnings.append(f"Volume has {len(entries)} selected entries; target is {target_count}.")
        return entries, warnings

    for ordinal, slug in enumerate(selected_slugs(manifest, posts)[:target_count]):
        post = posts_by_slug.get(slug)
        if not post:
            warnings.append(f"Selected entry is missing from docs/bits/posts: {slug}")
            continue
        byte_index = f"{ordinal:02X}"
        section_code = byte_index[0]
        art = art_entries.get(slug, {})
        layout_mode = str(art.get("layout_mode") or infer_layout_mode(post))
        entries.append(make_entry(byte_index, section_code, post, art, layout_mode, canonical_base))

    if len(entries) < target_count:
        warnings.append(f"Volume has {len(entries)} selected entries; target is {target_count}.")
    return entries, warnings


def make_entry(
    byte_index: str,
    section_code: str,
    post: BitPost,
    art: dict[str, Any],
    layout_mode: str,
    canonical_base: str,
) -> BookEntry:
    return BookEntry(
        byte_index=byte_index,
        section_code=section_code,
        bit=post,
        qr_target=f"{canonical_base}/{post.slug}/" if canonical_base else post.slug,
        art_status=str(art.get("status", "missing")),
        art_lane=str(art.get("lane", "auto_draft")),
        layout_mode=layout_mode,
        validation_notes=validate_entry(post, art),
    )


SECTION_HINTS: dict[str, list[str]] = {
    "0": [
        "signal",
        "static",
        "radio",
        "router",
        "telephone",
        "phone",
        "dial",
        "frequency",
        "transmission",
        "broadcast",
        "wire",
        "network",
    ],
    "1": ["room", "house", "archive", "desk", "lease", "paperwork", "memory", "chair", "domestic", "apartment"],
    "2": ["biological", "synthetic", "cell", "genome", "organism", "living", "flesh", "bloom", "honeycomb", "machine"],
    "3": ["bureaucracy", "municipal", "office", "form", "queue", "ministry", "clerk", "rule", "permit", "committee"],
    "4": ["ghost", "recursive", "recursion", "glitch", "duplicate", "afterimage", "haunting", "error", "loop"],
    "5": ["field", "elsewhere", "map", "route", "station", "expedition", "transcript", "report", "found", "edge"],
    "6": ["body", "anatomy", "medical", "ward", "bone", "flesh", "repair", "tissue", "blood", "ledger"],
    "7": ["time", "chrono", "clock", "calendar", "temporal", "delay", "loop", "12:15", "timekeeping"],
    "8": ["domestic", "ordinary", "object", "sock", "spoon", "chair", "card", "kitchen", "balcony", "home"],
    "9": ["ritual", "ceremony", "maintenance", "repair", "caretaker", "care", "recurring", "obligation", "instruction"],
    "A": ["archive", "archivist", "catalog", "index", "ledger", "preservation", "loss", "archaeology", "civilization"],
    "B": ["physics", "quantum", "matter", "material", "glass", "salt", "substance", "double-slit", "weave"],
    "C": ["instruction", "manual", "warning", "cipher", "key", "cryptographic", "code", "orders", "tune", "protocol"],
    "D": ["myth", "god", "pantheon", "psalter", "fable", "folklore", "ritual", "oracle"],
    "E": ["ending", "final", "last", "door", "threshold", "return", "irreversible", "absence", "forgetting"],
    "F": ["checksum", "proof", "validation", "signature", "book", "final entry", "meta", "release"],
}


THEME_SECTION_HINTS: dict[str, str] = {
    "abandoned stations": "5",
    "biological computing": "2",
    "consciousness frontiers": "2",
    "counterfeit realities": "4",
    "cryptographic secrets": "C",
    "digital archaeology": "A",
    "edge of maps": "5",
    "emergent intelligence": "2",
    "forgotten technology": "A",
    "lost civilizations": "A",
    "memory manipulation": "1",
    "maintenance myths": "9",
    "municipal weirdness": "3",
    "parallel dimensions": "4",
    "quantum mysteries": "B",
    "reality glitches": "4",
    "recursive realities": "4",
    "signal from nowhere": "0",
    "small gods of commerce": "8",
    "synthetic life": "2",
    "time anomalies": "7",
    "underground networks": "0",
}


LAYOUT_SECTION_HINTS: dict[str, str] = {
    "signal": "0",
    "archive": "A",
    "field_note": "5",
    "protocol": "3",
    "myth": "D",
    "glitch": "4",
}


def build_thematic_entries(
    manifest: dict[str, Any],
    posts: list[BitPost],
    art_entries: dict[str, dict[str, Any]],
    canonical_base: str,
    target_count: int,
) -> tuple[list[BookEntry], list[str]]:
    codes = section_codes(manifest)
    section_size = max(1, target_count // max(1, len(codes)))
    buckets: dict[str, list[tuple[BitPost, dict[str, Any], str]]] = {code: [] for code in codes}
    overflow: list[str] = []
    section_overrides = {
        str(slug): str(code).upper()
        for slug, code in (manifest.get("section_overrides") or {}).items()
    }

    for post in posts[:target_count]:
        art = art_entries.get(post.slug, {})
        layout_mode = str(art.get("layout_mode") or infer_layout_mode(post))
        override_code = section_overrides.get(post.slug)
        if override_code in buckets:
            if len(buckets[override_code]) < section_size:
                buckets[override_code].append((post, art, layout_mode))
                continue
            overflow.append(f"{post.slug} (section override {override_code} is full)")
            continue
        for code in ranked_sections(post, layout_mode, codes):
            if len(buckets[code]) < section_size:
                buckets[code].append((post, art, layout_mode))
                break
        else:
            overflow.append(post.slug)

    entries: list[BookEntry] = []
    for code in codes:
        for offset, (post, art, layout_mode) in enumerate(sorted(buckets[code], key=lambda item: item[0].date)):
            entries.append(make_entry(f"{code}{offset:X}", code, post, art, layout_mode, canonical_base))

    warnings = []
    if overflow:
        warnings.append(f"{len(overflow)} entries could not fit into their thematic 16-entry sections.")
    return entries, warnings


def ranked_sections(post: BitPost, layout_mode: str, codes: list[str]) -> list[str]:
    scores = section_scores(post, layout_mode, codes)
    return sorted(codes, key=lambda code: (-scores.get(code, 0), codes.index(code)))


def section_scores(post: BitPost, layout_mode: str, codes: list[str]) -> dict[str, int]:
    haystack = f"{post.title} {post.theme} {post.body[:1600]}".lower()
    scores = {code: 0 for code in codes}
    theme_code = THEME_SECTION_HINTS.get(post.theme.lower())
    if theme_code in scores:
        scores[theme_code] += 12
    layout_code = LAYOUT_SECTION_HINTS.get(layout_mode)
    if layout_code in scores:
        scores[layout_code] += 3
    for code, hints in SECTION_HINTS.items():
        if code not in scores:
            continue
        scores[code] += count_terms(haystack, hints)
    return scores


def infer_layout_mode(post: BitPost) -> str:
    haystack = f"{post.title} {post.theme} {post.body[:1200]}".lower()
    scores = {
        "signal": count_terms(haystack, ["signal", "static", "phone", "radio", "router", "transmission", "dial", "broadcast"]),
        "archive": count_terms(haystack, ["archive", "ledger", "memory", "catalog", "file", "index", "record", "library"]),
        "field_note": count_terms(haystack, ["field", "specimen", "report", "transcript", "interview", "route", "station", "map"]),
        "protocol": count_terms(haystack, ["protocol", "form", "rule", "queue", "clerk", "bureau", "office", "instruction"]),
        "myth": count_terms(haystack, ["myth", "god", "pantheon", "psalter", "ritual", "ceremony", "fable", "oracle"]),
        "glitch": count_terms(haystack, ["glitch", "recursive", "duplicate", "loop", "error", "quantum", "superposition", "reality"]),
    }
    return max(scores.items(), key=lambda item: (item[1], item[0]))[0] if max(scores.values()) > 0 else "archive"


def count_terms(text: str, terms: list[str]) -> int:
    return sum(text.count(term) for term in terms)


def validate_front_matter(manifest: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    front_matter = manifest.get("front_matter") or {}
    for key, config in front_matter.items():
        if not isinstance(config, dict) or not config.get("required_for_release"):
            continue
        source = Path(str(config.get("source", "")))
        title = str(config.get("title", key.replace("_", " ")))
        if not source.exists():
            warnings.append(f"{title} is required for release but missing at {source}.")
            continue
        if source.suffix.lower() == ".md":
            front, _body = parse_frontmatter(source.read_text())
            if front.get("status") == "draft":
                warnings.append(f"{title} exists but is still marked draft at {source}.")
    return warnings


def validate_entry(post: BitPost, art: dict[str, Any]) -> list[str]:
    notes: list[str] = []
    body_lower = post.body.lower()
    if re.search(r"\b(in the style of|as painted by|like a disney|marvel|star wars|pokemon)\b", body_lower):
        notes.append("Text needs IP review for style/franchise language.")
    if len(post.body.split()) < 100:
        notes.append("Entry body is unusually short.")
    if not art:
        notes.append("Art manifest entry is missing.")
    elif art.get("status") != "approved_for_book":
        notes.append(f"Art status is {art.get('status', 'missing')}; final release needs approved_for_book.")
    return notes


def collision_body(body: str) -> str:
    cleaned = strip_site_chrome(body)
    return "\n".join(line for line in cleaned.splitlines() if not line.lstrip().startswith("#"))


def likely_full_names(text: str) -> set[str]:
    phrase_stop = {
        "Air Loom",
        "Alexander Graham",
        "All Rights",
        "Alpha Subject",
        "Assistant Technician",
        "Bakelite Dial",
        "Buenos Aires",
        "Cold War",
        "Digital Archaeology",
        "Double Slit",
        "East Berlin",
        "Final Interview",
        "Grand Central",
        "Human Resources",
        "Memory Care",
        "New Jersey",
        "New York",
        "North Atlantic",
        "Protocol Nine",
        "Protocol Twelve",
        "Quantum Memory",
        "Research Center",
        "Rule Four",
        "Salt Archive",
        "Signal Corps",
        "Silent Garden",
        "Soviet Union",
        "Stasis Protocol",
        "Transfer Window",
        "Union Station",
        "Vatican Library",
        "Void Tuning",
    }
    first_word_stop = {
        "And",
        "But",
        "For",
        "From",
        "Her",
        "His",
        "Into",
        "Not",
        "Now",
        "Only",
        "That",
        "The",
        "Then",
        "They",
        "This",
        "When",
        "Where",
        "Which",
        "While",
        "With",
        "You",
        "Your",
    }
    domain_words = {
        "Archive",
        "Article",
        "Assistant",
        "Bureau",
        "Chapter",
        "Committee",
        "Department",
        "Director",
        "Document",
        "File",
        "Form",
        "Interview",
        "Ministry",
        "Office",
        "Protocol",
        "Rule",
        "Section",
        "Subject",
        "Technician",
        "Transcript",
        "Unit",
    }
    names: set[str] = set()
    for match in re.finditer(r"\b([A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,}){1,2})\b", text):
        name = re.sub(r"\s+", " ", match.group(1)).strip()
        parts = name.split()
        if name in phrase_stop or parts[0] in first_word_stop:
            continue
        if any(part in domain_words for part in parts):
            continue
        names.add(name)
    return names


def likely_single_names(text: str) -> set[str]:
    not_names = {
        "Above",
        "Addendum",
        "Again",
        "All",
        "Always",
        "Anomalies",
        "Approved",
        "Archive",
        "Archives",
        "Archivist",
        "Assistant",
        "Audit",
        "Back",
        "Bad",
        "Basement",
        "Because",
        "Before",
        "Behind",
        "Beneath",
        "Between",
        "Black",
        "Both",
        "Bring",
        "Bureau",
        "Can",
        "Chapter",
        "Check",
        "Clerk",
        "Collapse",
        "Come",
        "Committee",
        "Cut",
        "Days",
        "Dear",
        "Describe",
        "Department",
        "Did",
        "Director",
        "Document",
        "Don",
        "Each",
        "End",
        "Entry",
        "Even",
        "Every",
        "Everything",
        "Except",
        "File",
        "Final",
        "First",
        "Form",
        "Forms",
        "Found",
        "Fragments",
        "Ghost",
        "Hear",
        "Here",
        "How",
        "Incident",
        "Inside",
        "Instead",
        "Interview",
        "Interviewer",
        "Its",
        "Just",
        "Last",
        "Late",
        "Later",
        "Left",
        "Let",
        "Like",
        "Look",
        "Log",
        "Maintenance",
        "Mama",
        "Mars",
        "Maybe",
        "Meet",
        "Memories",
        "Memory",
        "Memo",
        "Ministry",
        "Morse",
        "Never",
        "Neither",
        "New",
        "Next",
        "Note",
        "Notes",
        "Not",
        "Nothing",
        "Now",
        "Nurse",
        "Office",
        "Okay",
        "Old",
        "One",
        "Only",
        "Our",
        "Outside",
        "Over",
        "Protocol",
        "Power",
        "Project",
        "Real",
        "Redacted",
        "Report",
        "Rule",
        "Same",
        "Said",
        "Section",
        "See",
        "Seventeen",
        "She",
        "Silence",
        "Smelled",
        "Some",
        "Someone",
        "Something",
        "Sometimes",
        "Somewhere",
        "Standard",
        "Static",
        "Still",
        "Stop",
        "Subject",
        "Technician",
        "Tell",
        "Temporal",
        "Then",
        "There",
        "Their",
        "These",
        "Those",
        "This",
        "Three",
        "Through",
        "Thursday",
        "Time",
        "Today",
        "Tonight",
        "Too",
        "Transcript",
        "Transfer",
        "Tuesday",
        "Two",
        "Typed",
        "Unknown",
        "Until",
        "Use",
        "Vault",
        "Wait",
        "Waiting",
        "Was",
        "What",
        "When",
        "Where",
        "Which",
        "Who",
        "Why",
        "Wires",
        "Years",
        "Yes",
        "Yesterday",
        "Yet",
        "Your",
        "Yours",
    }
    first_word_stop = {"And", "But", "For", "From", "Her", "His", "Into", "That", "The", "They", "This", "With", "You"}
    names: set[str] = set()
    for match in re.finditer(r"\b([A-Z][a-z]{2,})\b", text):
        name = match.group(1)
        if name in not_names or name in first_word_stop:
            continue
        previous = text[: match.start()].rstrip()
        if not previous or previous[-1] in ".?!\n":
            continue
        names.add(name)
    return names


def validate_name_collisions(entries: list[BookEntry], manifest: dict[str, Any]) -> tuple[list[str], list[str]]:
    validation = manifest.get("validation") or {}
    allowed_full = set(validation.get("allowed_repeated_full_names") or [])
    allowed_single = set(validation.get("allowed_repeated_single_names") or [])
    full_hits: dict[str, list[BookEntry]] = {}
    single_hits: dict[str, list[BookEntry]] = {}

    for entry in entries:
        body = collision_body(entry.bit.body)
        for name in likely_full_names(body):
            full_hits.setdefault(name, []).append(entry)
        for name in likely_single_names(body):
            single_hits.setdefault(name, []).append(entry)

    blockers: list[str] = []
    warnings: list[str] = []
    for name, hits in sorted(full_hits.items()):
        unique = unique_entries(hits)
        if len(unique) > 1 and name not in allowed_full:
            refs = ", ".join(f"{entry.byte_index} {entry.bit.slug}" for entry in unique)
            blockers.append(f"Repeated full-name candidate '{name}' appears in: {refs}.")
    for name, hits in sorted(single_hits.items()):
        unique = unique_entries(hits)
        if len(unique) > 2 and name not in allowed_single:
            refs = ", ".join(f"{entry.byte_index} {entry.bit.slug}" for entry in unique[:6])
            extra = f" (+{len(unique) - 6} more)" if len(unique) > 6 else ""
            warnings.append(f"Repeated single-name candidate '{name}' appears in {len(unique)} entries: {refs}{extra}.")
    return blockers, warnings


def unique_entries(entries: list[BookEntry]) -> list[BookEntry]:
    seen: set[str] = set()
    unique: list[BookEntry] = []
    for entry in entries:
        if entry.bit.slug in seen:
            continue
        seen.add(entry.bit.slug)
        unique.append(entry)
    return unique


def section_titles(manifest: dict[str, Any]) -> dict[str, str]:
    sections = manifest.get("sections") or []
    result: dict[str, str] = {}
    for section in sections:
        if isinstance(section, dict) and section.get("code") and section.get("title"):
            result[str(section["code"]).upper()] = str(section["title"])
    return result


def write_manuscript(entries: list[BookEntry], manifest: dict[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    sections = section_titles(manifest)
    lines = [
        f"# {manifest.get('title', '256 Bits')}",
        "",
        f"## {manifest.get('subtitle', 'Volume 1')}",
        "",
        "> Draft manuscript generated from repo content. Final PDF/ePub production still requires layout, art approval, QR generation, and human release review.",
        "",
    ]
    current_section = None
    for entry in entries:
        if entry.section_code != current_section:
            current_section = entry.section_code
            lines.extend(["", f"## {current_section}x: {sections.get(current_section, 'Untitled Section')}", ""])
        lines.extend(
            [
                f"### {entry.byte_index} - {entry.bit.title}",
                "",
                f"- Original date: {entry.bit.date}",
                f"- Theme: {entry.bit.theme or 'unmarked'}",
                f"- Generation ref: {entry.bit.generation_ref or 'unrecorded'}",
                f"- QR target: {entry.qr_target}",
                f"- Art: {entry.art_status} via {entry.art_lane}",
                f"- Book-edition text: {'yes' if entry.bit.book_text_edited else 'no'}",
                "",
                entry.bit.body,
                "",
            ]
        )
    output_path.write_text("\n".join(lines).rstrip() + "\n")


def write_validation_report(
    entries: list[BookEntry],
    warnings: list[str],
    review_warnings: list[str],
    manifest: dict[str, Any],
    output_path: Path,
) -> None:
    blockers = warnings[:]
    for entry in entries:
        blockers.extend(f"{entry.byte_index} {entry.bit.slug}: {note}" for note in entry.validation_notes)

    lines = [
        f"# Validation Report: {manifest.get('title', '256 Bits')}",
        "",
        f"Generated: {datetime.now().isoformat(timespec='seconds')}",
        "",
        f"Selected entries: {len(entries)} / {manifest.get('target_entry_count', 256)}",
        f"Blockers: {len(blockers)}",
        f"Review warnings: {len(review_warnings)}",
        "",
    ]
    if blockers:
        lines.append("## Blockers")
        lines.append("")
        lines.extend(f"- {item}" for item in blockers)
    else:
        lines.append("No blockers found by automated checks.")
    lines.append("")
    if review_warnings:
        lines.append("## Review Warnings")
        lines.append("")
        lines.extend(f"- {item}" for item in review_warnings)
        lines.append("")
    lines.append("## Required Human Checks")
    lines.append("")
    lines.extend(
        [
            "- Confirm every final entry is original enough for paid publication.",
            "- Confirm every final art asset has recorded prompt, provider, date, rights note, and human approval.",
            "- Confirm creator, machines, and editor notes are final and approved for publication.",
            "- Confirm QR targets resolve before upload to Gumroad.",
        ]
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines) + "\n")


def write_art_briefs(
    entries: list[BookEntry],
    output_path: Path,
    art_direction: dict[str, Any] | None = None,
    art_manifest: dict[str, Any] | None = None,
) -> None:
    art_direction = art_direction or {}
    art_manifest = art_manifest or {}
    briefs = {
        "volume": "256 Bits / Volume 1",
        "intent": "Every bit receives original art direction, even if the first pass is only a draft plate.",
        "entries": [art_brief_payload(entry, art_direction, art_manifest) for entry in entries],
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(yaml.safe_dump(briefs, sort_keys=False))


def write_art_priority_queue(
    entries: list[BookEntry],
    output_path: Path,
    art_direction: dict[str, Any],
    art_manifest: dict[str, Any],
) -> None:
    priority_rank = {"hero": 0, "high": 1, "medium": 2, "standard": 3}
    payloads = [art_brief_payload(entry, art_direction, art_manifest) for entry in entries]
    payloads.sort(key=lambda item: (priority_rank.get(str(item["priority"]), 9), item["byte_index"]))
    counts: dict[str, int] = {}
    lanes: dict[str, int] = {}
    for item in payloads:
        counts[str(item["priority"])] = counts.get(str(item["priority"]), 0) + 1
        lanes[str(item["recommended_lane"])] = lanes.get(str(item["recommended_lane"]), 0) + 1
    queue = {
        "volume": "256 Bits / Volume 1",
        "art_priority": "manual hero work first, then auto-draft coverage for every remaining story",
        "counts_by_priority": counts,
        "counts_by_recommended_lane": lanes,
        "batch_plan": [
            {
                "name": "batch_01_manual_hero",
                "goal": "Make the first sellable art spine of the book: cover candidates, section openers, and visually iconic stories.",
                "include_priorities": ["hero", "high"],
            },
            {
                "name": "batch_02_auto_draft_coverage",
                "goal": "Generate draft visual language for all remaining stories so every page can be designed against real art.",
                "include_priorities": ["medium", "standard"],
            },
            {
                "name": "batch_03_manual_replacements",
                "goal": "Replace weak draft plates with hand-directed final art after page-by-page review.",
                "include_priorities": ["medium", "standard"],
            },
        ],
        "queue": payloads,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(yaml.safe_dump(queue, sort_keys=False))


def write_manual_art_checklist(
    entries: list[BookEntry],
    output_path: Path,
    art_direction: dict[str, Any],
    art_manifest: dict[str, Any],
) -> None:
    priority_rank = {"hero": 0, "high": 1, "medium": 2, "standard": 3}
    payloads = [art_brief_payload(entry, art_direction, art_manifest) for entry in entries]
    payloads.sort(key=lambda item: (priority_rank.get(str(item["priority"]), 9), item["byte_index"]))
    manual = [item for item in payloads if item["priority"] in {"hero", "high"}]
    lines = [
        "# Manual Art Checklist",
        "",
        "This is the human-directed first art pass. These are the pieces most likely to define the cover, section openers, sales preview, and the first review loop.",
        "",
        "## Batch 01: Hero / High Priority",
        "",
    ]
    for item in manual:
        lines.extend(
            [
                f"### {item['byte_index']} - {item['title']}",
                "",
                f"- Slug: `{item['slug']}`",
                f"- Priority: `{item['priority']}`",
                f"- Recommended lane: `{item['recommended_lane']}`",
                f"- Expected use: {item['expected_use']}",
                f"- Treatment: {item['treatment']}",
                f"- Material: {item['material']}",
                f"- Gesture: {item['gesture']}",
                f"- Prompt: {item['prompt']}",
                f"- Negative prompt: {item['negative_prompt']}",
                "- Approval: missing / draft / needs_human_review / approved_for_book / rejected",
                "",
            ]
        )
    lines.extend(
        [
            "## Review Standard",
            "",
            "- The art should feel collected, not generated: tactile, specific, and materially tied to the story.",
            "- Prefer objects, diagrams, scans, specimens, traces, and strange evidence over literal character scenes.",
            "- Any visible text must be original, fragmentary, and intentionally designed.",
            "- Keep prompt/provider/date/edit history with the final asset.",
        ]
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines).rstrip() + "\n")


def manifest_path(path: Path | None) -> str:
    if path is None:
        return ""
    raw = str(path)
    if not path.is_absolute():
        return raw
    try:
        return str(path.resolve().relative_to(Path.cwd().resolve()))
    except ValueError:
        return raw


def write_source_manifest(entries: list[BookEntry], output_path: Path) -> None:
    payload = {
        "entries": [
            {
                "byte_index": entry.byte_index,
                "section_code": entry.section_code,
                "slug": entry.bit.slug,
                "title": entry.bit.title,
                "date": entry.bit.date,
                "source_path": manifest_path(entry.bit.path),
                "web_source_path": manifest_path(entry.bit.web_path or entry.bit.path),
                "book_source_path": manifest_path(entry.bit.book_source_path),
                "book_text_edited": entry.bit.book_text_edited,
                "generation_ref": entry.bit.generation_ref,
                "generation_url": entry.bit.generation_url,
                "qr_target": entry.qr_target,
                "art_status": entry.art_status,
                "layout_mode": entry.layout_mode,
            }
            for entry in entries
        ]
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def write_qr_targets(entries: list[BookEntry], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["byte_index", "slug", "title", "url"])
        writer.writeheader()
        for entry in entries:
            writer.writerow(
                {
                    "byte_index": entry.byte_index,
                    "slug": entry.bit.slug,
                    "title": entry.bit.title,
                    "url": entry.qr_target,
                }
            )


def write_candidate_scorecard(posts: list[BitPost], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "date",
                "slug",
                "title",
                "theme",
                "word_count",
                "curation_score",
                "ip_risk",
                "editor_notes",
                "selected",
            ],
        )
        writer.writeheader()
        for post in posts:
            writer.writerow(
                {
                    "date": post.date,
                    "slug": post.slug,
                    "title": post.title,
                    "theme": post.theme,
                    "word_count": len(post.body.split()),
                    "curation_score": "",
                    "ip_risk": "",
                    "editor_notes": "",
                    "selected": "",
                }
            )


def write_gumroad_readme(entries: list[BookEntry], manifest: dict[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    text = f"""Gumroad bundle staging area for {manifest.get('title', '256 Bits')}.

This draft currently includes {len(entries)} selected entries.

Final bundle checklist:
- 256-bits-volume-1.pdf
- 256-bits-volume-1.epub
- sample-edition.pdf
- art-pack/
- qr-index.pdf
- certificate.pdf
- license.txt
- readme.txt

Do not upload until validation-report.md has zero blockers.
"""
    output_path.write_text(text)


def build_book(volume_dir: Path, output_dir: Path) -> tuple[list[BookEntry], list[str]]:
    manifest = read_yaml(volume_dir / "manifest.yaml")
    art_manifest = read_yaml(volume_dir / "art_manifest.yaml")
    art_entries = load_art_entries(volume_dir / "art_manifest.yaml")
    art_direction = read_optional_yaml(volume_dir / ART_DIRECTION_FILE)
    posts = discover_bit_posts(editorial_dir=volume_dir / "stories")
    entries, warnings = build_entries(manifest, posts, art_entries)
    name_blockers, name_warnings = validate_name_collisions(entries, manifest)
    warnings = validate_front_matter(manifest) + warnings
    warnings.extend(name_blockers)
    outputs = manifest.get("release_outputs") or {}

    write_manuscript(entries, manifest, Path(outputs.get("manuscript", output_dir / "256-bits-volume-1.md")))
    write_validation_report(
        entries,
        warnings,
        name_warnings,
        manifest,
        Path(outputs.get("validation_report", output_dir / "validation-report.md")),
    )
    write_source_manifest(entries, Path(outputs.get("source_manifest", output_dir / "source-manifest.json")))
    write_art_briefs(
        entries,
        Path(outputs.get("art_briefs", output_dir / "art-briefs.yaml")),
        art_direction,
        art_manifest,
    )
    write_art_priority_queue(
        entries,
        Path(outputs.get("art_priority_queue", output_dir / "art-priority-queue.yaml")),
        art_direction,
        art_manifest,
    )
    write_manual_art_checklist(
        entries,
        Path(outputs.get("manual_art_checklist", output_dir / "manual-art-checklist.md")),
        art_direction,
        art_manifest,
    )
    write_qr_targets(entries, Path(outputs.get("qr_targets", output_dir / "qr-targets.csv")))
    write_candidate_scorecard(posts, Path(outputs.get("candidate_scorecard", output_dir / "candidate-scorecard.csv")))
    write_gumroad_readme(entries, manifest, Path(outputs.get("gumroad_readme", output_dir / "gumroad/README.txt")))
    return entries, warnings


def main() -> None:
    args = parse_args()
    entries, warnings = build_book(Path(args.volume_dir), Path(args.output_dir))
    target_count = read_yaml(Path(args.volume_dir) / "manifest.yaml").get("target_entry_count", 256)

    print(f"Built draft book artifacts for {len(entries)} / {target_count} entries.")
    for warning in warnings:
        print(f"WARNING: {warning}", file=sys.stderr)

    if warnings and not args.allow_incomplete:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
