#!/usr/bin/env python3
"""
Backfill the repo-backed link registry from existing published link posts.

Scans docs/links/posts/*.md, extracts URLs and metadata from frontmatter,
and registers them so future runs never duplicate a previously published link.

Usage:
    python scripts/backfill_registry.py
"""

import re
import sys
from pathlib import Path

# Ensure scripts/ is importable
sys.path.insert(0, str(Path(__file__).parent))

from link_registry import LinkRegistry


POSTS_DIR = Path("docs/links/posts")


def extract_links_from_post(filepath: Path):
    """Yield (url, title) tuples from a link post markdown file."""
    text = filepath.read_text()

    # Extract frontmatter fields
    theme = ""
    date_str = ""
    fm_match = re.match(r"^---\s*\n(.*?)\n---", text, re.S)
    if fm_match:
        fm = fm_match.group(1)
        m = re.search(r'^theme:\s*"?([^"\n]+)"?', fm, re.M)
        if m:
            theme = m.group(1).strip()
        m = re.search(r'^date:\s*(\S+)', fm, re.M)
        if m:
            date_str = m.group(1).strip()[:10]  # YYYY-MM-DD

    # Extract all visit-link URLs
    for m in re.finditer(r'<a\s+href="([^"]+)"[^>]*class="visit-link"', text):
        url = m.group(1)
        # Try to find the preceding ## title
        pos = m.start()
        title = ""
        title_match = re.search(r'##\s+\d+\.\s+(.+)', text[:pos][::-1][:500][::-1])
        # Simpler: search backwards for the nearest ## heading
        chunk = text[:pos]
        headings = re.findall(r'##\s+\d+\.\s+(.+)', chunk)
        if headings:
            title = headings[-1].strip()

        yield url, title, date_str, theme


def main():
    registry = LinkRegistry()

    if not POSTS_DIR.exists():
        print(f"No posts directory found at {POSTS_DIR}")
        return

    posts = sorted(POSTS_DIR.glob("*.md"))
    print(f"Found {len(posts)} link posts to scan")

    total = 0
    for post in posts:
        count = 0
        for url, title, date_str, theme in extract_links_from_post(post):
            if not registry.contains(url):
                registry.register(url, date_str, theme, title)
                count += 1
        total += count
        print(f"  {post.name}: registered {count} new URLs")

    registry.save()
    stats = registry.stats()
    print(f"\nRegistry backfill complete:")
    print(f"  Total links: {stats['total_links']}")
    print(f"  Unique domains: {stats['unique_domains']}")
    print(f"  Days tracked: {stats['days_tracked']}")
    if stats['top_domains']:
        print(f"  Top domains:")
        for domain, count in stats['top_domains'][:5]:
            print(f"    {domain}: {count}")


if __name__ == "__main__":
    main()
