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

PROMPTS_DIR = Path(__file__).parent.parent / "prompts"
THEMES_FILE = PROMPTS_DIR / "themes.yaml"
AUTO_THEME_ATTEMPTS = max(1, int(os.environ.get("AUTO_THEME_ATTEMPTS", "8")))
LINK_STEP_TIMEOUT_SECONDS = max(60, int(os.environ.get("RUN_DAILY_LINK_TIMEOUT_SECONDS", "900")))
STORY_STEP_TIMEOUT_SECONDS = max(60, int(os.environ.get("RUN_DAILY_STORY_TIMEOUT_SECONDS", "420")))
LANDING_STEP_TIMEOUT_SECONDS = max(30, int(os.environ.get("RUN_DAILY_LANDING_TIMEOUT_SECONDS", "180")))


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
    return yaml.safe_load(THEMES_FILE.read_text()) or {}


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
                if explicit_theme:
                    sys.exit(last_exit_code)
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
            run_script(
                "Generate Story",
                [sys.executable, str(scripts_dir / "generate_story.py"), "--theme-json", theme_json] + date_args,
                shared_env,
                timeout_seconds=STORY_STEP_TIMEOUT_SECONDS,
            )

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
