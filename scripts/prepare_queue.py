#!/usr/bin/env python3
"""Prepare staged editions under data/edition_queue for future publication."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Iterable, Optional

from project_paths import OUTPUT_ROOT_ENV, queue_entry_dir, queue_manifest_path


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare queued editions in staging output")
    parser.add_argument("--date", help="Single target date (YYYY-MM-DD)")
    parser.add_argument("--start-date", help="Start date for a queue window (YYYY-MM-DD)")
    parser.add_argument("--count", type=int, default=4, help="Number of consecutive dates to prepare")
    parser.add_argument("--force", action="store_true", help="Rebuild even if queue entry already looks complete")
    return parser.parse_args()


def resolve_date(value: Optional[str]) -> date:
    if not value:
        return date.today()
    return datetime.strptime(value, "%Y-%m-%d").date()


def iter_target_dates(args: argparse.Namespace) -> Iterable[date]:
    if args.date:
        yield resolve_date(args.date)
        return

    start = resolve_date(args.start_date)
    count = max(1, args.count)
    for offset in range(count):
        yield start + timedelta(days=offset)


def published_story_exists(target_date: date) -> bool:
    posts_dir = Path("docs/bits/posts")
    if not posts_dir.exists():
        return False
    return any(posts_dir.glob(f"{target_date.strftime('%Y-%m-%d')}-*.md"))


def published_links_exists(target_date: date) -> bool:
    return (Path("docs/links/posts") / f"{target_date.strftime('%Y-%m-%d')}-daily-links.md").exists()


def queued_story_path(entry_dir: Path, date_str: str) -> Optional[Path]:
    story_dir = entry_dir / "docs" / "bits" / "posts"
    matches = sorted(story_dir.glob(f"{date_str}-*.md"))
    return matches[0] if matches else None


def queued_links_path(entry_dir: Path, date_str: str) -> Optional[Path]:
    path = entry_dir / "docs" / "links" / "posts" / f"{date_str}-daily-links.md"
    return path if path.exists() else None


def queue_entry_complete(entry_dir: Path, target_date: date) -> bool:
    date_str = target_date.strftime("%Y-%m-%d")
    return bool(queued_story_path(entry_dir, date_str) and queued_links_path(entry_dir, date_str))


def extract_theme_name(path: Optional[Path]) -> str:
    if not path or not path.exists():
        return ""
    match = re.search(r'^theme:\s*"([^"]+)"', path.read_text(), re.MULTILINE)
    return match.group(1) if match else ""


def write_manifest(entry_dir: Path, payload: dict) -> None:
    manifest_path = entry_dir / "manifest.json"
    manifest_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def prepare_date(target_date: date, force: bool = False) -> int:
    date_str = target_date.strftime("%Y-%m-%d")
    entry_dir = queue_entry_dir(date_str)
    entry_dir.mkdir(parents=True, exist_ok=True)

    if not force and published_story_exists(target_date) and published_links_exists(target_date):
        print(f"Skipping {date_str}: already published")
        write_manifest(
            entry_dir,
            {
                "date": date_str,
                "status": "already-published",
                "updated_at": utc_now_iso(),
            },
        )
        return 0

    if not force and queue_entry_complete(entry_dir, target_date):
        print(f"Skipping {date_str}: queue entry already prepared")
        story_path = queued_story_path(entry_dir, date_str)
        links_path = queued_links_path(entry_dir, date_str)
        write_manifest(
            entry_dir,
            {
                "date": date_str,
                "status": "prepared",
                "theme": extract_theme_name(story_path) or extract_theme_name(links_path),
                "story_file": str(story_path.relative_to(entry_dir)) if story_path else "",
                "links_file": str(links_path.relative_to(entry_dir)) if links_path else "",
                "updated_at": utc_now_iso(),
            },
        )
        return 0

    env = os.environ.copy()
    env[OUTPUT_ROOT_ENV] = str(entry_dir)
    # Queue prep prioritizes reliability and bounded runtime over generating multiple drafts.
    env.setdefault("STORY_CANDIDATES", "1")
    env.setdefault("STORY_MODEL_ROUTING", "0")
    env.setdefault("OPENAI_REQUEST_TIMEOUT", "90")
    env.setdefault("ALLOW_CROSS_THEME_CORPUS_LINKS", "1")
    cmd = [
        sys.executable,
        str(Path(__file__).parent / "run_daily.py"),
        "--date",
        date_str,
        "--skip-landing",
    ]
    print(f"Preparing queue entry for {date_str}")
    result = subprocess.run(cmd, env=env)

    story_path = queued_story_path(entry_dir, date_str)
    links_path = queued_links_path(entry_dir, date_str)
    status = "prepared" if result.returncode == 0 and story_path and links_path else "failed"
    write_manifest(
        entry_dir,
        {
            "date": date_str,
            "status": status,
            "theme": extract_theme_name(story_path) or extract_theme_name(links_path),
            "story_file": str(story_path.relative_to(entry_dir)) if story_path else "",
            "links_file": str(links_path.relative_to(entry_dir)) if links_path else "",
            "exit_code": result.returncode,
            "updated_at": utc_now_iso(),
        },
    )

    if status == "prepared":
        print(f"Prepared queue entry: {queue_manifest_path(date_str)}")
        return 0

    print(f"Failed to prepare queue entry for {date_str}")
    return result.returncode or 1


def main() -> None:
    args = parse_args()
    exit_code = 0
    for target_date in iter_target_dates(args):
        result = prepare_date(target_date, force=args.force)
        if result != 0:
            exit_code = result
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
