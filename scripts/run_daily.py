#!/usr/bin/env python3
"""Orchestrate the full daily generation flow with a single command."""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

import yaml
from project_paths import links_posts_output_dir, story_posts_output_dir

try:
    from openai import OpenAI
    OPENAI_IMPORT_ERROR = None
except Exception as exc:
    OpenAI = None
    OPENAI_IMPORT_ERROR = exc

PROMPTS_DIR = Path(__file__).parent.parent / "prompts"
THEMES_FILE = PROMPTS_DIR / "themes.yaml"
API_BASE = os.environ.get("OPENAI_API_BASE", "https://integrate.api.nvidia.com/v1")
API_KEY = os.environ.get("OPENAI_API_KEY")
MODEL = os.environ.get("OPENAI_MODEL", "nvidia/llama-3.3-nemotron-super-49b-v1.5")
AUTO_THEME_ATTEMPTS = max(1, int(os.environ.get("AUTO_THEME_ATTEMPTS", "8")))
AI_THEME_FALLBACKS = max(0, int(os.environ.get("AI_THEME_FALLBACKS", "0")))
AI_THEME_TIMEOUT_SECONDS = max(15, int(os.environ.get("AI_THEME_TIMEOUT_SECONDS", "60")))
LINK_STEP_TIMEOUT_SECONDS = max(60, int(os.environ.get("RUN_DAILY_LINK_TIMEOUT_SECONDS", "900")))
STORY_STEP_TIMEOUT_SECONDS = max(60, int(os.environ.get("RUN_DAILY_STORY_TIMEOUT_SECONDS", "420")))
LANDING_STEP_TIMEOUT_SECONDS = max(30, int(os.environ.get("RUN_DAILY_LANDING_TIMEOUT_SECONDS", "180")))
ALLOW_EMPTY_LINKS = os.environ.get("ALLOW_EMPTY_LINKS", "0").lower() in {"1", "true", "yes"}
ALLOW_FALLBACK_STORY = os.environ.get("ALLOW_FALLBACK_STORY", "0").lower() in {"1", "true", "yes"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the full daily Obscure Bit generation pipeline")
    parser.add_argument("--theme-json", help="JSON string or path to JSON file specifying today's theme")
    parser.add_argument("--date", help="Override date (YYYY-MM-DD) when selecting from themes.yaml")
    parser.add_argument("--skip-story", action="store_true", help="Skip story generation")
    parser.add_argument("--skip-links", action="store_true", help="Skip link generation")
    parser.add_argument("--skip-landing", action="store_true", help="Skip landing + archive updates")
    return parser.parse_args()


def load_theme_override(raw_value: Optional[str]) -> Optional[dict]:
    source = raw_value or os.environ.get("THEME_JSON")
    if not source:
        return None
    try:
        text = source.strip()
        if text.startswith("{"):
            data = json.loads(text)
        else:
            potential = Path(text)
            if potential.exists():
                data = json.loads(potential.read_text())
            else:
                data = json.loads(text)
        print(f"Using theme override from input: {data.get('name', 'custom')}")
        return data
    except Exception as exc:
        print(f"⚠️  Failed to parse theme override: {exc}")
        return None


def load_themes() -> dict:
    if not THEMES_FILE.exists():
        print(f"Error: themes file not found at {THEMES_FILE}")
        sys.exit(1)
    config = yaml.safe_load(THEMES_FILE.read_text()) or {}
    overrides = config.get("overrides", {})
    config["overrides"] = {str(key): value for key, value in overrides.items()}
    return config


def resolve_target_date(date_override: Optional[str] = None) -> datetime:
    if date_override:
        try:
            return datetime.strptime(date_override, "%Y-%m-%d")
        except ValueError:
            print("Error: --date must be in YYYY-MM-DD format")
            sys.exit(1)
    return datetime.now()


def build_theme_candidates(date_override: Optional[str] = None, limit: int = AUTO_THEME_ATTEMPTS) -> List[Tuple[dict, str]]:
    config = load_themes()
    target_date = resolve_target_date(date_override)
    date_str = target_date.strftime("%Y-%m-%d")
    day_of_year = target_date.timetuple().tm_yday
    candidates: List[Tuple[dict, str]] = []
    seen_names = set()

    # Date-specific override
    overrides = config.get("overrides", {})
    if date_str in overrides:
        theme = overrides[date_str]
        theme_name = theme.get("name", "custom")
        candidates.append((theme, f"date override for {date_str}"))
        seen_names.add(theme_name.lower())

    themes = config.get("themes", [])
    if not themes:
        print("Error: no themes defined in themes.yaml")
        sys.exit(1)

    start_index = day_of_year % len(themes)
    for offset in range(len(themes)):
        theme = themes[(start_index + offset) % len(themes)]
        theme_name = theme.get("name", f"theme-{offset}")
        normalized_name = theme_name.lower()
        if normalized_name in seen_names:
            continue
        seen_names.add(normalized_name)
        label = (
            f"rotating theme for {date_str}"
            if offset == 0
            else f"rotating fallback +{offset} for {date_str}"
        )
        candidates.append((theme, label))
        if len(candidates) >= limit:
            break

    return candidates


def normalize_ai_theme(raw: dict) -> Optional[dict]:
    name = str(raw.get("name", "")).strip().lower()
    story = str(raw.get("story", "")).strip()
    links = str(raw.get("links", "")).strip()
    if not name or not story or not links:
        return None
    if len(name) > 60 or len(story) < 40 or len(links) < 30:
        return None
    return {"name": name, "story": story, "links": links}


def parse_ai_theme_response(content: str, limit: int) -> List[dict]:
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        start = content.find("[")
        end = content.rfind("]")
        if start < 0 or end <= start:
            return []
        try:
            parsed = json.loads(content[start:end + 1])
        except json.JSONDecodeError:
            return []

    if isinstance(parsed, dict):
        parsed = parsed.get("themes", [])
    if not isinstance(parsed, list):
        return []

    themes = []
    seen = set()
    for item in parsed:
        if not isinstance(item, dict):
            continue
        theme = normalize_ai_theme(item)
        if not theme:
            continue
        key = theme["name"].lower()
        if key in seen:
            continue
        seen.add(key)
        themes.append(theme)
        if len(themes) >= limit:
            break
    return themes


def generate_ai_theme_candidates(target_date: datetime, first_theme: dict, existing_names: set[str], limit: int) -> List[Tuple[dict, str]]:
    if limit <= 0:
        return []
    if not API_KEY:
        print("⚠️  AI theme fallback skipped: OPENAI_API_KEY not set")
        return []
    if OpenAI is None:
        print(f"⚠️  AI theme fallback skipped: OpenAI client unavailable: {OPENAI_IMPORT_ERROR}")
        return []

    date_str = target_date.strftime("%Y-%m-%d")
    prompt = f"""Generate {limit} fallback daily-edition themes for Obscure Bit.

The scheduled theme may fail link discovery:
{json.dumps(first_theme, indent=2)}

Return ONLY a JSON array. Each item must have:
- name: short lowercase theme label
- story: one sentence describing a grounded speculative story direction
- links: comma-separated search direction likely to find real obscure nonfiction links

Constraints:
- Optimize for discoverable obscure links: museum object pages, enthusiast research, primary documents, local history, old-web pages, field notes, manuals, archives.
- Avoid made-up proper nouns, fake incidents, exact named mysteries unless widely verifiable.
- Avoid broad physics/philosophy topics that mainly return generic explainers.
- Keep the Obscure Bit tone: specific jobs, places, records, tools, rituals, public systems.
- Do not reuse these theme names: {', '.join(sorted(existing_names))}
- Date: {date_str}
"""
    try:
        client = OpenAI(api_key=API_KEY, base_url=API_BASE, timeout=AI_THEME_TIMEOUT_SECONDS)
        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": "You design reliable fallback themes for a daily fiction-and-links site. Return valid JSON only."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.5,
            max_tokens=900,
            timeout=AI_THEME_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        print(f"⚠️  AI theme fallback failed: {exc}")
        return []

    content = response.choices[0].message.content or ""
    generated = parse_ai_theme_response(content, limit)
    candidates = []
    for theme in generated:
        key = theme["name"].lower()
        if key in existing_names:
            continue
        existing_names.add(key)
        candidates.append((theme, f"AI fallback theme for {date_str}"))
    if candidates:
        print(f"🤖 AI generated {len(candidates)} fallback theme(s)")
    else:
        print("⚠️  AI theme fallback returned no usable themes")
    return candidates


def enrich_theme_candidates_with_ai(candidates: List[Tuple[dict, str]], target_date: datetime) -> List[Tuple[dict, str]]:
    if AI_THEME_FALLBACKS <= 0 or not candidates:
        return candidates

    existing_names = {
        (theme.get("name") or "").lower()
        for theme, _label in candidates
        if theme.get("name")
    }
    first_theme = candidates[0][0]
    ai_candidates = generate_ai_theme_candidates(target_date, first_theme, existing_names, AI_THEME_FALLBACKS)
    if not ai_candidates:
        return candidates
    return [candidates[0], *ai_candidates, *candidates[1:]]


def print_theme(theme: dict, label: str = "Daily Theme") -> None:
    print(f"\n=== {label} ===")
    print(json.dumps(theme, indent=2))


def find_existing_story(target_date: datetime) -> Optional[Path]:
    posts_dir = story_posts_output_dir()
    if not posts_dir.exists():
        return None
    date_prefix = target_date.strftime("%Y-%m-%d")
    matches = sorted(posts_dir.glob(f"{date_prefix}-*.md"))
    return matches[0] if matches else None


def find_existing_links(target_date: datetime) -> Optional[Path]:
    path = links_posts_output_dir() / f"{target_date.strftime('%Y-%m-%d')}-daily-links.md"
    return path if path.exists() else None


def markdown_escape(value: str) -> str:
    return value.replace('"', '\\"')


def slugify(value: str) -> str:
    slug = value.lower()
    slug = "".join(c if c.isalnum() or c == " " else "" for c in slug)
    return "-".join(slug.split()[:7]) or "fallback-bit"


def write_empty_links(theme: dict, target_date: datetime, reason: str) -> Path:
    date_str = target_date.strftime("%Y-%m-%d")
    theme_name = theme.get("name", "unknown")
    output_dir = links_posts_output_dir()
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{date_str}-daily-links.md"
    content = f"""---
date: {target_date}
title: "Obscure Links - {target_date.strftime('%B %d, %Y')}"
description: "Today's link discovery did not produce publishable links: {markdown_escape(reason)}"
author: "Obscure Bit"
theme: "{markdown_escape(theme_name)}"
---

# Obscure Links - {target_date.strftime('%B %d, %Y')}

Link discovery did not produce publishable links for this edition.

The daily story still published; this page is intentionally empty rather than filled with weak or off-theme links.
"""
    path.write_text(content)

    context_dir = Path("data/discovery/story_context")
    context_dir.mkdir(parents=True, exist_ok=True)
    context_path = context_dir / f"{date_str}-links.json"
    context_path.write_text(json.dumps({
        "date": date_str,
        "theme": theme_name,
        "motifs": [],
        "interesting_bits": [],
        "links": [],
        "fallback_reason": reason,
    }, indent=2) + "\n")
    print(f"⚠️  Wrote empty links fallback: {path}")
    print(f"⚠️  Wrote empty story context fallback: {context_path}")
    return path


def fallback_story_text(theme: dict, target_date: datetime) -> tuple[str, str, str]:
    theme_name = theme.get("name", "unknown")
    title = f"The Spare Edition"
    genre = "Fallback speculative vignette"
    date_label = target_date.strftime("%B %d, %Y")
    story = f"""By the time the daily machine admitted it had no story, the office had already opened.

The clerk on duty was supposed to stamp a packet, unlock the side door, and pretend the missing page did not matter. Instead, she held the blank sheet up to the window and watched the morning pass through it. On one side was {date_label}. On the other was the version of the day that had arrived fully prepared.

The form at the top said {theme_name.title()}. Nobody in the queue cared what that meant. They cared about lunch breaks, bus transfers, small promises made too early, and whether a system that failed politely still counted as a system.

So she wrote the first true thing she could prove: the day had happened. Then she wrote the second: someone had noticed.

At closing, she filed the page between the finished editions and locked the cabinet. The blank space did not disappear. It became part of the record, which was not the same as being repaired, but was better than being lost."""
    return title, story, genre


def write_fallback_story(theme: dict, target_date: datetime, reason: str) -> Path:
    date_str = target_date.strftime("%Y-%m-%d")
    theme_name = theme.get("name", "unknown")
    title, story, genre = fallback_story_text(theme, target_date)
    safe_title = markdown_escape(title)
    safe_theme = markdown_escape(theme_name)
    safe_genre = markdown_escape(genre)
    output_dir = story_posts_output_dir()
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{date_str}-{slugify(title)}.md"
    content = f"""---
date: {date_str}
title: "{safe_title}"
description: "Fallback daily story generated after model failure: {markdown_escape(reason)}"
author: "fallback-local"
theme: "{safe_theme}"
genre: "{safe_genre}"
---

# {title}

{story}
"""
    path.write_text(content)
    print(f"⚠️  Wrote fallback story after generation failure: {path}")
    return path


def run_script(
    label: str,
    command: list[str],
    env: dict,
    *,
    exit_on_failure: bool = True,
    timeout_seconds: Optional[int] = None,
) -> int:
    print(f"\n▶ {label}: {' '.join(command)}")
    try:
        result = subprocess.run(command, env=env, timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        print(f"❌ {label} timed out after {timeout_seconds}s")
        if exit_on_failure:
            sys.exit(124)
        return 124
    if result.returncode != 0:
        print(f"❌ {label} failed (exit code {result.returncode})")
        if exit_on_failure:
            sys.exit(result.returncode)
        return result.returncode
    print(f"✅ {label} completed")
    return 0


def main():
    args = parse_args()

    scripts_dir = Path(__file__).parent
    date_args = ["--date", args.date] if args.date else []
    target_date = resolve_target_date(args.date)
    explicit_theme = load_theme_override(args.theme_json)
    theme_candidates = (
        [(explicit_theme, "explicit theme override")]
        if explicit_theme
        else build_theme_candidates(args.date)
    )
    if not explicit_theme:
        theme_candidates = enrich_theme_candidates_with_ai(theme_candidates, target_date)
    theme = theme_candidates[0][0]
    theme_json = json.dumps(theme)
    shared_env = os.environ.copy()
    shared_env["THEME_JSON"] = theme_json

    if explicit_theme:
        print(f"Using explicit theme override: {theme.get('name', 'custom')}")
    else:
        print(f"Using {theme_candidates[0][1]}: {theme.get('name', 'unknown')}")
    print_theme(theme)

    if not args.skip_links:
        existing_links = find_existing_links(target_date)
        if existing_links:
            print(f"Using existing links for {target_date.strftime('%Y-%m-%d')}: {existing_links}")
        else:
            last_exit_code = 1
            for index, (candidate_theme, source_label) in enumerate(theme_candidates, start=1):
                theme = candidate_theme
                theme_json = json.dumps(theme)
                shared_env = os.environ.copy()
                shared_env["THEME_JSON"] = theme_json

                if index > 1:
                    print(f"\n⚠️  Retrying with {source_label}: {theme.get('name', 'unknown')}")
                    print_theme(theme, label="Fallback Theme")

                last_exit_code = run_script(
                    "Generate Links",
                    [sys.executable, str(scripts_dir / "generate_links.py"), "--theme-json", theme_json] + date_args,
                    shared_env,
                    exit_on_failure=False,
                    timeout_seconds=LINK_STEP_TIMEOUT_SECONDS,
                )
                if last_exit_code == 0:
                    if index > 1:
                        print(f"Using fallback theme for remaining steps: {theme.get('name', 'unknown')}")
                    break
                if explicit_theme and not ALLOW_EMPTY_LINKS:
                    sys.exit(last_exit_code)
            else:
                if ALLOW_EMPTY_LINKS:
                    reason = (
                        f"link generation failed for {len(theme_candidates)} attempted theme(s) "
                        f"with exit code {last_exit_code}"
                    )
                    write_empty_links(theme, target_date, reason)
                else:
                    print(
                        f"Error: link generation failed for {len(theme_candidates)} attempted theme(s) "
                        f"on {(args.date or datetime.now().strftime('%Y-%m-%d'))}"
                    )
                    sys.exit(last_exit_code)

    if not args.skip_story:
        existing_story = find_existing_story(target_date)
        if existing_story:
            print(f"Using existing story for {target_date.strftime('%Y-%m-%d')}: {existing_story}")
        else:
            story_exit_code = run_script(
                "Generate Story",
                [sys.executable, str(scripts_dir / "generate_story.py"), "--theme-json", theme_json] + date_args,
                shared_env,
                exit_on_failure=False,
                timeout_seconds=STORY_STEP_TIMEOUT_SECONDS,
            )
            if story_exit_code != 0:
                if ALLOW_FALLBACK_STORY:
                    write_fallback_story(theme, target_date, f"story generation exited {story_exit_code}")
                else:
                    sys.exit(story_exit_code)

    if not args.skip_landing:
        run_script(
            "Update Landing",
            [sys.executable, str(scripts_dir / "update_landing.py"), "--theme-json", theme_json] + date_args,
            shared_env,
            timeout_seconds=LANDING_STEP_TIMEOUT_SECONDS,
        )

    print("\n🎉 Daily pipeline finished successfully!")


if __name__ == "__main__":
    main()
