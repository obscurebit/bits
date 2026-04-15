#!/usr/bin/env python3
"""
Generate a daily AI story for Obscure Bit.
Uses OpenAI-compatible API endpoints.
"""

import os
import sys
import json
import random
import hashlib
import argparse
import re
from datetime import datetime
from pathlib import Path
from typing import Optional
from collections import Counter

import yaml
try:
    from openai import OpenAI
    OPENAI_IMPORT_ERROR = None
except Exception as exc:
    OpenAI = None
    OPENAI_IMPORT_ERROR = exc
from discovery_corpus import STORY_CONTEXT_DIR

# Configuration
API_BASE = os.environ.get("OPENAI_API_BASE", "https://integrate.api.nvidia.com/v1")
API_KEY = os.environ.get("OPENAI_API_KEY")
MODEL = os.environ.get("OPENAI_MODEL", "nvidia/llama-3.3-nemotron-super-49b-v1.5")
SELECTOR_MODEL = os.environ.get("STORY_SELECTOR_MODEL", MODEL)
ENABLE_MODEL_ROUTING = os.environ.get("STORY_MODEL_ROUTING", "1").lower() not in {"0", "false", "no"}

# Paths to prompt files
PROMPTS_DIR = Path(__file__).parent.parent / "prompts"
SYSTEM_PROMPT_FILE = PROMPTS_DIR / "story_system.md"
SELECTOR_PROMPT_FILE = PROMPTS_DIR / "story_selector_system.md"
MODEL_ROUTING_FILE = PROMPTS_DIR / "story_model_routing.yaml"
THEMES_FILE = PROMPTS_DIR / "themes.yaml"
STYLE_MODIFIERS_FILE = PROMPTS_DIR / "style_modifiers.yaml"
POSTS_DIR = Path("docs/bits/posts")

STORY_CANDIDATES = max(1, int(os.environ.get("STORY_CANDIDATES", "2")))
RECENT_STORY_LOOKBACK = max(4, int(os.environ.get("STORY_RECENT_LOOKBACK", "12")))

STYLE_KEYS = [
    "pov",
    "tone",
    "era",
    "setting",
    "structure",
    "conflict",
    "opening",
    "genre",
    "wildcard",
    "protagonist",
    "desire",
    "anchor_object",
    "social_pressure",
    "ending_shape",
]

COMMON_WORDS = {
    "about", "after", "again", "against", "almost", "another", "around", "because",
    "before", "being", "between", "could", "every", "found", "going", "little",
    "maybe", "might", "never", "nothing", "other", "people", "really", "should",
    "something", "still", "their", "there", "these", "thing", "those", "through",
    "until", "where", "which", "while", "would", "your", "from", "into", "than",
    "them", "they", "were", "when", "with", "have", "that", "this", "then", "just",
    "like", "over", "under", "once", "here", "what", "said", "some", "very", "more",
}

CANDIDATE_EMPHASES = [
    "Lean grounded and human-scale. Let the strange thing pressure a job, debt, promise, friendship, or family obligation.",
    "Lean sly and funny without turning into parody. Favor precise details over lore.",
    "Lean emotionally sharper and quieter. Let one choice or confession do the heavy lifting.",
    "Lean tactile and place-specific. Make the world feel inhabited before it feels uncanny.",
    "Lean structurally adventurous, but keep it readable. Surprise through form only if it reveals character.",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate the daily Obscure Bit story")
    parser.add_argument("--theme-json", help="JSON string or path to JSON file specifying today's theme")
    parser.add_argument("--date", help="Override date (YYYY-MM-DD) for backfill generation")
    return parser.parse_args()


def resolve_date(date_override: Optional[str] = None) -> datetime:
    """Return the target date, either from an override string or today."""
    if date_override:
        return datetime.strptime(date_override, "%Y-%m-%d")
    return datetime.now()


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
        print(f"Using theme override: {data.get('name', 'custom')}")
        return data
    except Exception as e:
        print(f"Warning: failed to parse theme override: {e}")
        return None


def load_system_prompt() -> str:
    """Load the system prompt from external file."""
    if not SYSTEM_PROMPT_FILE.exists():
        print(f"Error: System prompt file not found at {SYSTEM_PROMPT_FILE}")
        sys.exit(1)
    return SYSTEM_PROMPT_FILE.read_text().strip()


def load_selector_prompt() -> str:
    """Load the selector prompt from external file."""
    if not SELECTOR_PROMPT_FILE.exists():
        print(f"Error: Selector prompt file not found at {SELECTOR_PROMPT_FILE}")
        sys.exit(1)
    return SELECTOR_PROMPT_FILE.read_text().strip()


def load_themes() -> dict:
    """Load unified themes configuration from YAML file."""
    if not THEMES_FILE.exists():
        print(f"Error: Themes file not found at {THEMES_FILE}")
        sys.exit(1)
    return yaml.safe_load(THEMES_FILE.read_text()) or {}


def load_model_routing() -> dict:
    """Load optional story model routing configuration from YAML."""
    if not MODEL_ROUTING_FILE.exists():
        return {}
    return yaml.safe_load(MODEL_ROUTING_FILE.read_text()) or {}


def get_daily_theme(target_date: Optional[datetime] = None) -> dict:
    """Get today's theme (story + links directions)."""
    selected_date = target_date or datetime.now()
    date_str = selected_date.strftime("%Y-%m-%d")
    day_of_year = selected_date.timetuple().tm_yday
    
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


def load_style_modifiers() -> dict:
    """Load style modifier pools from YAML file."""
    if not STYLE_MODIFIERS_FILE.exists():
        print(f"Warning: Style modifiers file not found at {STYLE_MODIFIERS_FILE}")
        return {}
    return yaml.safe_load(STYLE_MODIFIERS_FILE.read_text()) or {}


def get_daily_seed(target_date: Optional[datetime] = None) -> int:
    """Generate a deterministic-but-unique seed from a date.
    
    Uses a hash so the same date always produces the same story constraints,
    but different dates produce wildly different selections.
    """
    dt = target_date or datetime.now()
    date_str = dt.strftime("%Y-%m-%d")
    return int(hashlib.sha256(date_str.encode()).hexdigest(), 16)


def select_style_modifiers(target_date: Optional[datetime] = None) -> dict:
    """Pick one random option from each style dimension, seeded by date."""
    modifiers = load_style_modifiers()
    if not modifiers:
        return {}
    
    rng = random.Random(get_daily_seed(target_date))
    
    selected = {}
    for key in STYLE_KEYS:
        options = modifiers.get(key, [])
        if options:
            selected[key] = rng.choice(options)
    
    # Select a banned word set
    banned_sets = modifiers.get("banned_word_sets", [])
    if banned_sets:
        selected["banned_words"] = rng.choice(banned_sets)
    
    return selected


def normalize_signal(text: Optional[str]) -> str:
    return (text or "").lower().strip()


def build_model_routing_signals(theme: dict, style: dict) -> dict:
    signals = {
        "theme_name": normalize_signal(theme.get("name")),
        "theme_story": normalize_signal(theme.get("story")),
    }
    for key in STYLE_KEYS:
        signals[key] = normalize_signal(style.get(key))
    return signals


def route_match_score(route: dict, signals: dict) -> int:
    score = 0
    for field, terms in (route.get("match") or {}).items():
        signal = signals.get(field, "")
        if not signal:
            continue
        for term in terms or []:
            if normalize_signal(term) in signal:
                score += 1
    return score


def select_story_model(theme: dict, style: dict) -> tuple[str, str]:
    """Select a writer model from routing rules based on the chosen brief."""
    if not ENABLE_MODEL_ROUTING:
        return MODEL, "routing-disabled"

    routing = load_model_routing()
    routes = routing.get("routes", [])
    if not routes:
        return MODEL, "default"

    signals = build_model_routing_signals(theme, style)
    best_route = None
    best_score = 0

    for route in routes:
        score = route_match_score(route, signals)
        if score > best_score:
            best_route = route
            best_score = score

    if best_route and best_route.get("model"):
        route_name = best_route.get("name", "unnamed-route")
        return best_route["model"], f"{route_name}:{best_score}"

    return MODEL, "default"


def strip_markdown_artifacts(text: str) -> str:
    cleaned = text.strip().lstrip("#").strip()
    cleaned = re.sub(r"^[*_`]+", "", cleaned)
    cleaned = re.sub(r"[*_`]+$", "", cleaned)
    return cleaned.strip()


def clean_story_response(content: str) -> str:
    cleaned = content.strip()

    if "<think>" in cleaned and "</think>" in cleaned:
        think_end = cleaned.find("</think>")
        cleaned = cleaned[think_end + len("</think>"):].strip()

    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()

    return cleaned


def parse_story_output(content: str) -> tuple[str, str]:
    lines = content.splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)

    if not lines:
        return "Untitled Story", content.strip()

    title = strip_markdown_artifacts(lines[0])
    body_lines = lines[1:]
    while body_lines and not body_lines[0].strip():
        body_lines.pop(0)

    story = "\n".join(body_lines).strip() or content.strip()
    return title, story


def extract_story_body(markdown_text: str) -> str:
    try:
        _, remainder = markdown_text.split("\n---\n", 1)
        _, body = remainder.split("\n# ", 1)
        body = body.split("\n", 1)[1]
    except ValueError:
        body = markdown_text

    body = body.split("\n---\n<div", 1)[0]
    return body.strip()


def parse_frontmatter_value(markdown_text: str, key: str) -> Optional[str]:
    match = re.search(rf'^{re.escape(key)}:\s+"([^"]+)"', markdown_text, re.MULTILINE)
    return match.group(1).strip() if match else None


def meaningful_words(text: str) -> list[str]:
    words = []
    for raw in re.findall(r"[A-Za-z][A-Za-z'-]{3,}", text.lower()):
        token = raw.strip("-'")
        if len(token) < 4 or token in COMMON_WORDS:
            continue
        words.append(token)
    return words


def collect_recent_story_context(target_date: Optional[datetime] = None, limit: int = RECENT_STORY_LOOKBACK) -> dict:
    if not POSTS_DIR.exists():
        return {}

    skip_prefix = (target_date or datetime.now()).strftime("%Y-%m-%d")
    post_paths = [
        path for path in sorted(POSTS_DIR.glob("*.md"))
        if not path.name.startswith(skip_prefix)
    ]
    recent_paths = post_paths[-limit:]
    if not recent_paths:
        return {}

    titles = []
    openings = []
    themes = Counter()
    genres = Counter()
    title_words = Counter()
    body_words = Counter()

    for path in recent_paths:
        text = path.read_text()
        title = parse_frontmatter_value(text, "title") or path.stem
        theme = parse_frontmatter_value(text, "theme")
        genre = parse_frontmatter_value(text, "genre")
        body = extract_story_body(text)

        titles.append(title)
        if theme:
            themes[theme] += 1
        if genre:
            genres[genre] += 1

        first_paragraph = next(
            (part.strip() for part in body.split("\n\n") if part.strip() and not part.strip().startswith("<div")),
            "",
        )
        if first_paragraph:
            openings.append(re.sub(r"\s+", " ", first_paragraph)[:140])

        title_words.update(meaningful_words(title))
        body_words.update(meaningful_words(body))

    repeated_title_words = [word for word, count in title_words.most_common() if count > 1][:8]
    repeated_body_words = [word for word, count in body_words.most_common() if count > 2][:12]
    repeated_themes = [name for name, count in themes.most_common() if count > 1][:4]
    repeated_genres = [name for name, count in genres.most_common() if count > 1][:4]

    return {
        "titles": titles[-6:],
        "openings": openings[-4:],
        "repeated_title_words": repeated_title_words,
        "repeated_body_words": repeated_body_words,
        "repeated_themes": repeated_themes,
        "repeated_genres": repeated_genres,
    }


def format_recent_story_context(context: dict) -> str:
    if not context:
        return ""

    parts = ["RECENT ARCHIVE TO AVOID REPEATING:"]

    if context.get("titles"):
        titles = "; ".join(context["titles"])
        parts.append(f"- Recent titles: {titles}")

    if context.get("openings"):
        openings = " | ".join(context["openings"])
        parts.append(f"- Recent opening moves already used: {openings}")

    repeated_terms = context.get("repeated_title_words", []) + context.get("repeated_body_words", [])
    if repeated_terms:
        deduped_terms = list(dict.fromkeys(repeated_terms))[:14]
        parts.append(f"- Recently overused words or images: {', '.join(deduped_terms)}")

    if context.get("repeated_themes"):
        parts.append(f"- Themes showing up lately: {', '.join(context['repeated_themes'])}")

    if context.get("repeated_genres"):
        parts.append(f"- Genre flavors showing up lately: {', '.join(context['repeated_genres'])}")

    parts.append("- Do not write another archive/signal/bureaucracy/mysterious-device story unless you reinvent it so thoroughly it no longer reads like the archive above.")
    return "\n".join(parts)


def load_daily_link_context(target_date: Optional[datetime] = None) -> dict:
    date_str = (target_date or datetime.now()).strftime("%Y-%m-%d")
    filepath = STORY_CONTEXT_DIR / f"{date_str}-links.json"
    if not filepath.exists():
        return {}
    try:
        return json.loads(filepath.read_text())
    except Exception as exc:
        print(f"Warning: failed to read link context: {exc}")
        return {}


def format_link_context(link_context: dict) -> str:
    if not link_context:
        return ""

    parts = ["TODAY'S REAL-WEB INSPIRATION (borrow texture, not plot):"]
    motifs = link_context.get("motifs", [])
    interesting_bits = link_context.get("interesting_bits", [])
    links = link_context.get("links", [])

    if motifs:
        parts.append(f"- Motifs from today's selected links: {', '.join(motifs[:10])}")
    if interesting_bits:
        parts.append(f"- Concrete odd details surfaced by the web crawl: {' | '.join(interesting_bits[:5])}")
    if links:
        compact = []
        for item in links[:3]:
            title = item.get("title", "").strip()
            reason = item.get("reason", "").strip()
            if title and reason:
                compact.append(f"{title} ({reason})")
            elif title:
                compact.append(title)
        if compact:
            parts.append(f"- Keep the story in dialogue with this discovered material: {'; '.join(compact)}")
    parts.append("- Do not retell the linked pages. Let them influence imagery, occupations, objects, social texture, or emotional pressure.")
    return "\n".join(parts)


def select_candidate_emphases(target_date: Optional[datetime], count: int) -> list[str]:
    rng = random.Random(get_daily_seed(target_date) ^ 0x5F3759DF)
    options = CANDIDATE_EMPHASES[:]
    rng.shuffle(options)
    if count <= len(options):
        return options[:count]

    selected = options
    while len(selected) < count:
        selected.append(options[len(selected) % len(options)])
    return selected


def build_story_prompt(theme: dict, style: dict, target_date: Optional[datetime] = None) -> tuple[str, str]:
    """Generate a unique prompt for today's story based on theme + randomized style modifiers.
    
    Returns (prompt_text, genre_label) so genre can be stored in frontmatter.
    """
    story_direction = theme.get("story", theme.get("name", "mysterious technology"))
    recent_context = format_recent_story_context(collect_recent_story_context(target_date))
    link_context = format_link_context(load_daily_link_context(target_date))
    
    parts = [
        "Write one short speculative fiction story for Obscure Bit.",
        f"Theme seed: {story_direction}",
    ]
    parts.append("")
    
    if style:
        parts.append("TODAY'S BRIEF (make these feel organic, not bolted on):")
        if "protagonist" in style:
            parts.append(f"- Protagonist: {style['protagonist']}")
        if "desire" in style:
            parts.append(f"- What they want: {style['desire']}")
        if "social_pressure" in style:
            parts.append(f"- Social pressure around them: {style['social_pressure']}")
        if "anchor_object" in style:
            parts.append(f"- Anchor object: {style['anchor_object']}")
        if "pov" in style:
            parts.append(f"- Point of view: {style['pov']}")
        if "tone" in style:
            parts.append(f"- Tone: {style['tone']}")
        if "era" in style:
            parts.append(f"- Setting era: {style['era']}")
        if "setting" in style:
            parts.append(f"- Setting location: {style['setting']}")
        if "structure" in style:
            parts.append(f"- Narrative structure: {style['structure']}")
        if "conflict" in style:
            parts.append(f"- Central conflict: {style['conflict']}")
        if "opening" in style:
            parts.append(f"- Opening: {style['opening']}")
        if "genre" in style:
            parts.append(f"- Genre flavor: {style['genre']}")
        if "wildcard" in style:
            parts.append(f"- Wildcard constraint: {style['wildcard']}")
        if "ending_shape" in style:
            parts.append(f"- Ending shape: {style['ending_shape']}")
        if "banned_words" in style:
            words = ", ".join(style["banned_words"])
            parts.append(f"- BANNED WORDS (do not use these): {words}")
        parts.append("")
    
    parts.append("CRAFT TARGETS:")
    parts.append("- Give the story a lived-in social world: work, family, neighbors, debt, ritual, status, care, jealousy, obligation, or embarrassment.")
    parts.append("- Use concrete, specific details that feel observed rather than generated.")
    parts.append("- Let the speculative element change a choice, relationship, or small power dynamic on the page.")
    parts.append("- Keep the title fresh, short, and concrete. Avoid titles built from overused abstract nouns.")
    parts.append("- Prefer one memorable emotional turn over a pile of lore.")

    if recent_context:
        parts.append("")
        parts.append(recent_context)

    if link_context:
        parts.append("")
        parts.append(link_context)
    
    prompt = "\n".join(parts)
    genre = style.get("genre", "speculative fiction")
    print(f"Style modifiers: {json.dumps(style, indent=2, default=str)}")
    return prompt, genre


def build_candidate_prompts(base_prompt: str, target_date: Optional[datetime] = None, count: int = STORY_CANDIDATES) -> list[str]:
    prompts = []
    for emphasis in select_candidate_emphases(target_date, count):
        prompts.append(f"{base_prompt}\n\nDRAFT-SPECIFIC NUDGE:\n- {emphasis}")
    return prompts


def request_story_completion(client: OpenAI, writer_model: str, system_prompt: str, user_prompt: str, temperature: float) -> str:
    response = client.chat.completions.create(
        model=writer_model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=temperature,
        top_p=0.95,
        max_tokens=4096,
    )
    return clean_story_response(response.choices[0].message.content.strip())


def build_story_selection_prompt(theme: dict, base_prompt: str, candidates: list[str], target_date: Optional[datetime] = None) -> str:
    recent_context = format_recent_story_context(collect_recent_story_context(target_date))
    parts = [
        f"Theme: {theme.get('name', 'unknown')}",
        f"Generation brief summary: {theme.get('story', theme.get('name', 'unknown'))}",
        "Choose the candidate most worth publishing.",
        "Prefer the story that feels most original, most specific, least templated, and least similar to the recent archive.",
        "Penalize generic mystery scaffolding, lore dumps, placeholder weirdness, and AI-sounding cadences.",
    ]

    if recent_context:
        parts.extend(["", recent_context])

    parts.extend(["", "ORIGINAL WRITING BRIEF USED FOR ALL CANDIDATES:", base_prompt])

    for index, candidate in enumerate(candidates, start=1):
        parts.extend(["", f"CANDIDATE {index}", candidate])

    return "\n".join(parts)


def select_best_candidate(client: OpenAI, theme: dict, base_prompt: str, candidates: list[str], target_date: Optional[datetime] = None) -> int:
    if len(candidates) == 1:
        return 0

    selector_prompt = load_selector_prompt()
    selection_prompt = build_story_selection_prompt(theme, base_prompt, candidates, target_date)

    response = client.chat.completions.create(
        model=SELECTOR_MODEL,
        messages=[
            {"role": "system", "content": selector_prompt},
            {"role": "user", "content": selection_prompt},
        ],
        temperature=0.15,
        top_p=0.9,
        max_tokens=250,
    )

    decision = clean_story_response(response.choices[0].message.content.strip())
    print(f"Selector decision: {decision}")

    match = re.search(r'"winner"\s*:\s*(\d+)', decision)
    if not match:
        match = re.search(r"\b([1-9]\d*)\b", decision)

    if not match:
        return 0

    winner = int(match.group(1)) - 1
    if 0 <= winner < len(candidates):
        return winner
    return 0


def generate_story(theme: dict, target_date: Optional[datetime] = None) -> tuple[str, str, str, str, str]:
    """Generate a story using the OpenAI-compatible API.
    
    Returns (title, story, theme_name, genre, writer_model).
    """
    if not API_KEY:
        print("Error: OPENAI_API_KEY environment variable not set")
        sys.exit(1)
    if OpenAI is None:
        print(f"Error: OpenAI client unavailable: {OPENAI_IMPORT_ERROR}")
        sys.exit(1)
    
    client = OpenAI(
        api_key=API_KEY,
        base_url=API_BASE,
    )
    
    system_prompt = load_system_prompt()
    style = select_style_modifiers(target_date)
    writer_model, routing_reason = select_story_model(theme, style)
    user_prompt, genre = build_story_prompt(theme, style, target_date)
    
    print(f"System prompt loaded from: {SYSTEM_PROMPT_FILE}")
    print(f"Writer model selected: {writer_model} ({routing_reason})")

    candidates = []
    candidate_prompts = build_candidate_prompts(user_prompt, target_date, STORY_CANDIDATES)
    for index, candidate_prompt in enumerate(candidate_prompts, start=1):
        temperature = min(1.0, 0.82 + ((index - 1) * 0.06))
        print(f"Generating candidate {index}/{len(candidate_prompts)} at temperature {temperature:.2f}")
        candidates.append(request_story_completion(client, writer_model, system_prompt, candidate_prompt, temperature))

    selected_index = select_best_candidate(client, theme, user_prompt, candidates, target_date)
    content = candidates[selected_index]
    print(f"Selected candidate: {selected_index + 1}/{len(candidates)}")

    title, story = parse_story_output(content)
    
    theme_name = theme.get("name", "unknown")
    
    return title, story, theme_name, genre, writer_model


def save_story(title: str, story: str, theme_name: str, genre: str = "speculative fiction", writer_model: Optional[str] = None, target_date: Optional[datetime] = None) -> Path:
    """Save the story as a markdown file in the bits/posts directory."""
    today = target_date or datetime.now()
    date_str = today.strftime("%Y-%m-%d")
    safe_title = title.replace('"', '\\"')
    safe_theme = theme_name.replace('"', '\\"')
    safe_genre = genre.replace('"', '\\"')
    safe_author = f"{API_BASE} / {writer_model or MODEL}".replace('"', '\\"')
    
    # Create slug from title
    slug = title.lower()
    slug = "".join(c if c.isalnum() or c == " " else "" for c in slug)
    slug = "-".join(slug.split()[:6])
    
    # Ensure posts directory exists
    posts_dir = Path("docs/bits/posts")
    posts_dir.mkdir(parents=True, exist_ok=True)
    
    # Create filename
    filename = f"{date_str}-{slug}.md"
    filepath = posts_dir / filename
    
    # Get git commit hash
    import subprocess
    try:
        commit_hash = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True
        ).stdout.strip()
        commit_url = f"https://github.com/obscurebit/b1ts/tree/{commit_hash}"
    except:
        commit_hash = "unknown"
        commit_url = "#"
    
    # Create markdown file
    frontmatter = f"""---
date: {date_str}
title: "{safe_title}"
description: "A daily AI-generated story exploring speculative fiction"
author: "{safe_author}"
theme: "{safe_theme}"
genre: "{safe_genre}"
---

"""
    content = f"""{frontmatter}# {title}

{story}

---

<div style="display: flex; justify-content: space-between; align-items: center; margin-top: 2rem;">
  <button class="share-btn" data-url="{{% raw %}}{{{{ page.canonical_url }}}}{{% endraw %}}" data-title="{title}">
    Share this story
  </button>
  <a href="{commit_url}" target="_blank" rel="noopener" class="story-gen-link">
    gen:{commit_hash}
  </a>
</div>
"""
    
    filepath.write_text(content)
    print(f"Story saved to: {filepath}")
    return filepath


def main():
    args = parse_args()
    target_date = resolve_date(args.date) if args.date else None
    theme_override = load_theme_override(args.theme_json)
    theme = theme_override or get_daily_theme(target_date)
    date_label = (target_date or datetime.now()).strftime("%Y-%m-%d")
    print(f"Generating story for {date_label}...")
    print(f"Using API base: {API_BASE}")
    print(f"Using model: {MODEL}")
    print(f"Using selector model: {SELECTOR_MODEL}")
    print(f"Story model routing: {'enabled' if ENABLE_MODEL_ROUTING else 'disabled'}")
    print(f"Story candidates: {STORY_CANDIDATES}")
    print(f"Theme: {theme.get('name', 'unknown')}")
    
    title, story, theme_name, genre, writer_model = generate_story(theme, target_date)
    print(f"Generated story: {title}")
    print(f"Genre: {genre}")
    print(f"Writer model: {writer_model}")
    
    filepath = save_story(title, story, theme_name, genre, writer_model, target_date)
    print(f"Success! Story saved to {filepath}")


if __name__ == "__main__":
    main()
