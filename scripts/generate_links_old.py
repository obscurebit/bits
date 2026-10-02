#!/usr/bin/env python3
"""
Generate daily obscure links for Obscure Bit.
Uses OpenAI-compatible API endpoints to discover and summarize interesting content.
"""

import os
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Dict

import yaml
import requests
from openai import OpenAI

# Configuration
API_BASE = os.environ.get("OPENAI_API_BASE", "https://integrate.api.nvidia.com/v1")
API_KEY = os.environ.get("OPENAI_API_KEY")
MODEL = os.environ.get("OPENAI_MODEL", "nvidia/nemotron-3-ultra-550b-a55b")

# Paths to prompt files
PROMPTS_DIR = Path(__file__).parent.parent / "prompts"
SYSTEM_PROMPT_FILE = PROMPTS_DIR / "links_system.md"
THEMES_FILE = PROMPTS_DIR / "themes.yaml"


def load_system_prompt() -> str:
    """Load the system prompt from external file."""
    if not SYSTEM_PROMPT_FILE.exists():
        print(f"Error: System prompt file not found at {SYSTEM_PROMPT_FILE}")
        sys.exit(1)
    return SYSTEM_PROMPT_FILE.read_text().strip()


def load_themes() -> dict:
    """Load unified themes configuration from YAML file."""
    if not THEMES_FILE.exists():
        print(f"Error: Themes file not found at {THEMES_FILE}")
        sys.exit(1)
    return yaml.safe_load(THEMES_FILE.read_text()) or {}


def get_daily_theme() -> dict:
    """Get today's theme (story + links directions)."""
    today = datetime.now()
    date_str = today.strftime("%Y-%m-%d")
    day_of_year = today.timetuple().tm_yday
    
    config = load_themes()
    
    # Check for date-specific override
    overrides = config.get("overrides", {})
    if date_str in overrides:
        theme = overrides[date_str]
        print(f"Using theme override for {date_str}: {theme.get('name', 'custom')}")
        return theme
    
    # Use rotating themes
    themes = config.get("themes", [])
    if not themes:
        print("Error: No themes found in themes.yaml")
        sys.exit(1)
    
    theme = themes[day_of_year % len(themes)]
    print(f"Using theme: {theme.get('name', 'unknown')}")
    return theme


def get_links_prompt() -> str:
    """Generate a unique prompt for today's links."""
    theme = get_daily_theme()
    links_direction = theme.get("links", "obscure discoveries")
    
    return f"""Find 5-7 obscure but real links related to: {links_direction}

These should be genuine URLs that exist and work. Focus on content that feels like a hidden discovery - something readers wouldn't stumble upon through normal browsing.

Remember: Only real, verifiable links from your training data. Prefer .edu, .gov, archive.org, Wikipedia, or well-established sites."""


def validate_url(url: str) -> bool:
    """Validate that a URL is accessible with a GET request."""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }
    try:
        # Disable SSL verification to avoid LibreSSL compatibility issues
        # This is just for checking if URLs exist, not for secure data transfer
        response = requests.head(url, timeout=10, allow_redirects=True, headers=headers, verify=False)
        if response.status_code < 400:
            return True
        
        # If HEAD fails, try GET (some servers don't support HEAD)
        response = requests.get(url, timeout=10, allow_redirects=True, headers=headers, verify=False)
        return response.status_code < 400
    except Exception as e:
        print(f"  ✗ Failed to validate {url}: {str(e)[:50]}")
        return False


def generate_links() -> tuple[List[Dict], str]:
    """Generate links using the OpenAI-compatible API."""
    if not API_KEY:
        print("Error: OPENAI_API_KEY environment variable not set")
        sys.exit(1)
    
    client = OpenAI(
        api_key=API_KEY,
        base_url=API_BASE,
    )
    
    system_prompt = load_system_prompt()
    user_prompt = get_links_prompt()
    
    print(f"System prompt loaded from: {SYSTEM_PROMPT_FILE}")
    
    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.6,
        top_p=0.95,
        max_tokens=4096,
    )
    
    content = response.choices[0].message.content.strip()
    
    # Strip </think>...</think> reasoning blocks (used by some models like Nemotron)
    if "</think>" in content and "</think>" in content:
        think_end = content.find("</think>")
        content = content[think_end + len("</think>"):].strip()
    
    print(f"\nModel response:\n{'-'*60}\n{content[:500]}\n{'-'*60}\n")
    
    # Parse the links
    links = []
    current_link = {}
    
    for line in content.split("\n"):
        line = line.strip()
        if line.startswith("LINK:"):
            if current_link:
                links.append(current_link)
            current_link = {"url": line[5:].strip()}
        elif line.startswith("TITLE:"):
            current_link["title"] = line[6:].strip()
        elif line.startswith("SUMMARY:"):
            current_link["summary"] = line[8:].strip()
        elif line.startswith("WHY:"):
            current_link["why"] = line[4:].strip()
    
    if current_link and "url" in current_link:
        links.append(current_link)
    
    # Validate URLs
    print(f"\nValidating {len(links)} links...")
    validated_links = []
    for link in links:
        url = link.get("url", "")
        if url:
            print(f"  Checking: {url}")
            if validate_url(url):
                print(f"  ✓ Valid")
                validated_links.append(link)
            else:
                print(f"  ✗ Invalid or unreachable, skipping")
    
    print(f"\nValidated {len(validated_links)}/{len(links)} links")
    
    # If we have at least 3 valid links, use them. Otherwise, use all links (validation may be too strict)
    if len(validated_links) >= 3:
        final_links = validated_links
    else:
        print(f"Warning: Only {len(validated_links)} links validated. Using all {len(links)} links anyway.")
        final_links = links
    
    # Get theme name
    theme = get_daily_theme()
    theme_name = theme.get("name", "unknown")
    
    return final_links, theme_name


def save_links(links: List[Dict], theme_name: str) -> Path:
    """Save the links as a markdown file in the links/posts directory."""
    today = datetime.now()
    date_str = today.strftime("%Y-%m-%d")
    
    # Ensure posts directory exists
    posts_dir = Path("docs/links/posts")
    posts_dir.mkdir(parents=True, exist_ok=True)
    
    # Create filename
    filename = f"{date_str}-daily-links.md"
    filepath = posts_dir / filename
    
    # Build markdown content
    links_content = ""
    for i, link in enumerate(links, 1):
        title = link.get("title", f"Link {i}")
        url = link.get("url", "#")
        summary = link.get("summary", "")
        why = link.get("why", "")
        
        links_content += f"""
## {i}. {title}

{summary}

*{why}*

<a href="{url}" target="_blank" rel="noopener" class="visit-link">Visit Link →</a>

---
"""
    
    frontmatter = f"""---
date: {today}
title: "Obscure Links - {today.strftime('%B %d, %Y')}"
description: "Today's curated obscure links from the hidden corners of the web"
author: "{API_BASE} / {MODEL}"
theme: "{theme_name}"
---

"""
    
    content = f"""{frontmatter}
# Obscure Links - {today.strftime('%B %d, %Y')}

Today's curated discoveries from the hidden corners of the web.

{links_content}

<button class="share-btn" data-url="{{% raw %}}{{{{ page.canonical_url }}}}{{% endraw %}}" data-title="Obscure Links - {date_str}">
  Share today's links
</button>
"""
    
    filepath.write_text(content)
    print(f"Links saved to: {filepath}")
    return filepath


def main():
    print("Generating daily links...")
    print(f"Using API base: {API_BASE}")
    print(f"Using model: {MODEL}")
    
    links, theme_name = generate_links()
    print(f"Generated {len(links)} links")
    
    if not links:
        print("Warning: No links generated")
        sys.exit(1)
    
    filepath = save_links(links, theme_name)
    print(f"Success! Links saved to {filepath}")


if __name__ == "__main__":
    main()
