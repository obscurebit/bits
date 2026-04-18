#!/usr/bin/env python3
"""Promote a prepared edition from data/edition_queue into published docs output."""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Optional, Tuple

from project_paths import queue_entry_dir, queue_manifest_path
from update_landing import (
    create_edition_snapshot,
    get_edition_number,
    get_links_for_date,
    get_story_for_date,
    update_bits_index,
    update_editions_index,
    update_home_html,
    update_links_index,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Publish a prepared queued edition")
    parser.add_argument("--date", help="Target date to publish (YYYY-MM-DD)")
    parser.add_argument("--update-home", action="store_true", help="Update home page to this edition")
    return parser.parse_args()


def resolve_date(value: Optional[str]) -> date:
    if not value:
        return date.today()
    return datetime.strptime(value, "%Y-%m-%d").date()


def published_story_path(target_date: date) -> Optional[Path]:
    matches = sorted(Path("docs/bits/posts").glob(f"{target_date.strftime('%Y-%m-%d')}-*.md"))
    return matches[0] if matches else None


def published_links_path(target_date: date) -> Optional[Path]:
    path = Path("docs/links/posts") / f"{target_date.strftime('%Y-%m-%d')}-daily-links.md"
    return path if path.exists() else None


def queued_story_path(entry_dir: Path, target_date: date) -> Optional[Path]:
    matches = sorted((entry_dir / "docs" / "bits" / "posts").glob(f"{target_date.strftime('%Y-%m-%d')}-*.md"))
    return matches[0] if matches else None


def queued_links_path(entry_dir: Path, target_date: date) -> Optional[Path]:
    path = entry_dir / "docs" / "links" / "posts" / f"{target_date.strftime('%Y-%m-%d')}-daily-links.md"
    return path if path.exists() else None


def remove_existing_for_date(target_date: date) -> None:
    date_str = target_date.strftime("%Y-%m-%d")
    story_dir = Path("docs/bits/posts")
    links_dir = Path("docs/links/posts")
    for path in story_dir.glob(f"{date_str}-*.md"):
        path.unlink()
    link_path = links_dir / f"{date_str}-daily-links.md"
    if link_path.exists():
        link_path.unlink()


def copy_prepared_files(target_date: date) -> Tuple[Path, Path]:
    entry_dir = queue_entry_dir(target_date.strftime("%Y-%m-%d"))
    story_source = queued_story_path(entry_dir, target_date)
    links_source = queued_links_path(entry_dir, target_date)
    if not story_source or not links_source:
        raise FileNotFoundError(f"Queue entry incomplete for {target_date}")

    remove_existing_for_date(target_date)

    story_target_dir = Path("docs/bits/posts")
    links_target_dir = Path("docs/links/posts")
    story_target_dir.mkdir(parents=True, exist_ok=True)
    links_target_dir.mkdir(parents=True, exist_ok=True)

    story_target = story_target_dir / story_source.name
    links_target = links_target_dir / links_source.name
    shutil.copy2(story_source, story_target)
    shutil.copy2(links_source, links_target)
    return story_target, links_target


def update_queue_manifest(target_date: date, story_path: Path, links_path: Path) -> None:
    manifest_path = queue_manifest_path(target_date.strftime("%Y-%m-%d"))
    payload = {}
    if manifest_path.exists():
        try:
            payload = json.loads(manifest_path.read_text())
        except json.JSONDecodeError:
            payload = {}
    payload.update(
        {
            "status": "published",
            "published_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "published_story_file": str(story_path),
            "published_links_file": str(links_path),
        }
    )
    manifest_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def rebuild_site_state(target_date: date, update_home: bool) -> None:
    edition = get_edition_number(target_date)
    story = get_story_for_date(target_date)
    links, total_links = get_links_for_date(target_date)
    if update_home:
        theme = {"name": story.get("theme")} if story and story.get("theme") else None
        update_home_html(story, links, total_links, edition, theme)
    create_edition_snapshot(edition, story, links, {"name": story.get("theme")} if story else None, target_date)
    update_bits_index()
    update_links_index()
    update_editions_index()


def main() -> None:
    args = parse_args()
    target_date = resolve_date(args.date)
    update_home = args.update_home or target_date == date.today()

    story_path = published_story_path(target_date)
    links_path = published_links_path(target_date)
    if story_path and links_path:
        print(f"Using already-published content for {target_date}")
        rebuild_site_state(target_date, update_home)
        return

    story_path, links_path = copy_prepared_files(target_date)
    update_queue_manifest(target_date, story_path, links_path)
    print(f"Published prepared content for {target_date}:")
    print(f"  Story: {story_path}")
    print(f"  Links: {links_path}")
    rebuild_site_state(target_date, update_home)


if __name__ == "__main__":
    main()
