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


DEFAULT_VOLUME_DIR = Path("book/volume-1")
DEFAULT_OUTPUT_DIR = Path("book-output/volume-1")
BIT_POSTS_DIR = Path("docs/bits/posts")


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
    return "\n".join(cleaned_lines).strip()


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


def discover_bit_posts(posts_dir: Path = BIT_POSTS_DIR) -> list[BitPost]:
    posts: list[BitPost] = []
    for path in sorted(posts_dir.glob("*.md")):
        text = path.read_text()
        frontmatter, body = parse_frontmatter(text)
        slug = path.stem
        date_value = frontmatter.get("date") or slug[:10]
        title = clean_title(frontmatter.get("title") or title_from_body(body) or slug)
        generation_ref, generation_url = extract_generation_ref(body)
        posts.append(
            BitPost(
                path=path,
                slug=slug,
                date=date_value,
                title=title,
                description=frontmatter.get("description", ""),
                theme=frontmatter.get("theme", ""),
                body=strip_site_chrome(body),
                generation_ref=generation_ref,
                generation_url=generation_url,
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


def load_art_entries(path: Path) -> dict[str, dict[str, Any]]:
    data = read_yaml(path)
    entries = data.get("entries") or []
    result: dict[str, dict[str, Any]] = {}
    for item in entries:
        if isinstance(item, dict) and item.get("slug"):
            result[str(item["slug"])] = item
    return result


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

    for ordinal, slug in enumerate(selected_slugs(manifest, posts)[:target_count]):
        post = posts_by_slug.get(slug)
        if not post:
            warnings.append(f"Selected entry is missing from docs/bits/posts: {slug}")
            continue
        byte_index = f"{ordinal:02X}"
        section_code = byte_index[0]
        art = art_entries.get(slug, {})
        art_status = str(art.get("status", "missing"))
        art_lane = str(art.get("lane", "auto_draft"))
        layout_mode = str(art.get("layout_mode") or infer_layout_mode(post))
        notes = validate_entry(post, art)
        entries.append(
            BookEntry(
                byte_index=byte_index,
                section_code=section_code,
                bit=post,
                qr_target=f"{canonical_base}/{post.slug}/" if canonical_base else post.slug,
                art_status=art_status,
                art_lane=art_lane,
                layout_mode=layout_mode,
                validation_notes=notes,
            )
        )

    if len(entries) < target_count:
        warnings.append(f"Volume has {len(entries)} selected entries; target is {target_count}.")
    return entries, warnings


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


def write_art_briefs(entries: list[BookEntry], output_path: Path) -> None:
    briefs = {
        "entries": [
            {
                "byte_index": entry.byte_index,
                "slug": entry.bit.slug,
                "title": entry.bit.title,
                "original_date": entry.bit.date,
                "layout_mode": entry.layout_mode,
                "lane": entry.art_lane,
                "status": entry.art_status,
                "brief": f"Create original {entry.layout_mode.replace('_', ' ')} art for '{entry.bit.title}' without logos, celebrity likenesses, franchise references, or living-artist style imitation.",
                "forbidden": [
                    "living artist style imitation",
                    "logos",
                    "celebrity likenesses",
                    "named fictional universes",
                    "copied reference images",
                ],
            }
            for entry in entries
        ]
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(yaml.safe_dump(briefs, sort_keys=False))


def write_source_manifest(entries: list[BookEntry], output_path: Path) -> None:
    payload = {
        "entries": [
            {
                "byte_index": entry.byte_index,
                "slug": entry.bit.slug,
                "title": entry.bit.title,
                "date": entry.bit.date,
                "source_path": str(entry.bit.path),
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
    art_entries = load_art_entries(volume_dir / "art_manifest.yaml")
    posts = discover_bit_posts()
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
    write_art_briefs(entries, Path(outputs.get("art_briefs", output_dir / "art-briefs.yaml")))
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
