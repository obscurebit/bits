# Development Guide

Technical documentation for developers working on Obscure Bit.

## Quick Start

```bash
# Clone and bootstrap the local venv with uv
git clone https://github.com/obscurebit/b1ts.git
cd b1ts
uv venv
uv pip install --python .venv/bin/python -r requirements.txt
```

`uv` is the default local runner for this repo. The project still keeps a simple `requirements.txt`, but local execution should assume `.venv` + `uv run` so your shell Python does not become part of the runtime.

## Local Development

### Serve Locally

```bash
# Start development server with live reload
uv run --python .venv/bin/python mkdocs serve

# Or if mkdocs is in PATH
mkdocs serve
```

Site available at: **http://127.0.0.1:8000**

Changes to files in `docs/` and `overrides/` will auto-reload.

### Build Site

```bash
# Build static site to site/ directory
uv run --python .venv/bin/python mkdocs build

# Preview built site (no live reload)
python3 -m http.server 8000 --directory site
```

### Common Issues

**`mkdocs: command not found`**
```bash
# Use the project venv through uv
uv run --python .venv/bin/python mkdocs serve
```

**Missing dependencies**
```bash
uv pip install --python .venv/bin/python -r requirements.txt
```

**Shell Python is missing packages like `yaml`**
```bash
# Do not rely on the system interpreter for project scripts
uv run --python .venv/bin/python scripts/run_daily.py --help
```

## Environment Setup

### Required Environment Variables

```bash
export OPENAI_API_KEY="your-nvidia-nim-api-key"
export OPENAI_API_BASE="https://integrate.api.nvidia.com/v1"
export OPENAI_MODEL="nvidia/llama-3.3-nemotron-super-49b-v1.5"
export STORY_MODEL_ROUTING="1"                          # Route writer model by story brief
export STORY_CANDIDATES="2"                              # Generate multiple drafts and auto-pick the strongest
export STORY_SELECTOR_MODEL="$OPENAI_MODEL"             # Optional separate editor model
```

For story quality A/B tests on `build.nvidia.com`, keep the prompts fixed and swap only `OPENAI_MODEL`. Two worthwhile candidates to compare against the current default are `qwen/qwen3-next-80b-a3b-instruct` and `mistralai/mistral-large-3-675b-instruct-2512`.

Link discovery is intentionally lane-first. The nightly job does not use generic search-result APIs; it starts from curated source neighborhoods, expands with bounded crawl, and curates from the repo-backed corpus.

### GitHub Secrets (for CI)

| Secret | Description |
|--------|-------------|
| `OPENAI_API_KEY` | NVIDIA NIM API key |
| `OPENAI_API_BASE` | API endpoint (optional) |
| `OPENAI_MODEL` | Model name (optional) |
| `STORY_MODEL_ROUTING` | Enable brief-based writer model routing |
| `STORY_CANDIDATES` | Number of story drafts to generate before selection |
| `STORY_SELECTOR_MODEL` | Optional separate model for picking the best draft |

## Scripts

### Content Generation

```bash
# Full daily run (story + links + landing)
uv run --python .venv/bin/python scripts/run_daily.py

# Generate a new story
uv run --python .venv/bin/python scripts/generate_story.py

# Generate new links
uv run --python .venv/bin/python scripts/generate_links.py

# Update landing pages and archives
uv run --python .venv/bin/python scripts/update_landing.py
```

### Link Registry

The link generation system now persists discovery memory directly in the repository under `data/discovery/`. That lets nightly GitHub Actions runs accumulate candidates over time instead of starting from zero.

```bash
# Seed the link registry from all existing published link posts
uv run --python .venv/bin/python scripts/backfill_registry.py

# The registry and discovery corpus are automatically updated after each generate_links.py run
```

Repo-backed discovery files:

- `data/discovery/link_registry.json` - hard dedup for previously published URLs
- `data/discovery/candidates.jsonl` - compact corpus of discovered candidate pages
- `data/discovery/selection_history.jsonl` - nightly selection history for novelty penalties
- `data/discovery/domain_state.json` - domain freshness/frequency tracking
- `data/discovery/story_context/` - same-day motifs exported for story generation

In CI, the backfill step remains as a safety bootstrap if the registry ever falls behind the published posts.

### Backfill Past Dates

All generation scripts accept `--date YYYY-MM-DD` to generate content for a specific date. The date controls theme selection, style modifier seed, and output filenames.

```bash
# Full backfill for a specific date (skip landing since it should reflect latest)
uv run --python .venv/bin/python scripts/run_daily.py --date 2026-02-10 --skip-landing

# Backfill just a story
uv run --python .venv/bin/python scripts/generate_story.py --date 2026-02-10

# Backfill just links
uv run --python .venv/bin/python scripts/generate_links.py --date 2026-02-10

# Then rebuild archives to include the new content
uv run --python .venv/bin/python scripts/update_landing.py
```

### Substack Publishing

Substack uses Cloudflare protection that blocks automated requests. We use a two-script approach:

#### 1. Cookie Extraction (One-time setup)

```bash
# Install Playwright (one-time)
uv pip install --python .venv/bin/python playwright
uv run --python .venv/bin/python playwright install chromium

# Login and extract cookies (opens browser)
uv run --python .venv/bin/python scripts/substack_playwright.py --login

# Export cookies for GitHub Actions (optional)
uv run --python .venv/bin/python scripts/substack_playwright.py --export-cookies
```

Cookies are saved to `~/.substack_cookies.json`.

#### 2. Publishing with API

```bash
# Set environment variables
export SUBSTACK_PUBLICATION_URL="https://obscurebit.substack.com"
export SUBSTACK_COOKIES_PATH="$HOME/.substack_cookies.json"

# Create draft for edition
uv run --python .venv/bin/python scripts/publish_substack.py --edition 3 --draft

# Publish directly
uv run --python .venv/bin/python scripts/publish_substack.py --edition 3 --publish

# Force republish
uv run --python .venv/bin/python scripts/publish_substack.py --edition 3 --publish --force
```

#### Alternative: Manual Cookie Export

1. Log in to substack.com in your browser
2. Open Developer Tools → Application → Cookies → substack.com
3. Click "Export all as JSON" or copy manually
4. Save to file: `~/.substack_cookies.json`
5. Set env: `export SUBSTACK_COOKIES_PATH="$HOME/.substack_cookies.json"`

## Project Structure

```
b1ts/
├── .github/workflows/
│   ├── deploy.yml              # GitHub Pages deployment
│   └── generate-content.yml    # Daily content generation (6 AM UTC)
├── docs/
│   ├── bits/posts/             # Daily stories (YYYY-MM-DD-slug.md)
│   ├── links/posts/            # Daily links
│   ├── editions/posts/         # Daily edition snapshots
│   ├── substack/               # Newsletter drafts & markers
│   ├── stylesheets/            # Custom CSS
│   └── javascripts/            # Custom JS
├── scripts/
│   ├── run_daily.py            # Orchestrator (theme → story + links + landing)
│   ├── generate_story.py       # AI story generation w/ style modifiers
│   ├── generate_links.py       # Lane-first obscure link discovery + repo-backed corpus
│   ├── discovery_corpus.py     # Persistent discovery memory + novelty-aware selection
│   ├── link_registry.py        # Persistent SHA-256 URL registry for cross-day dedup
│   ├── backfill_registry.py    # Seeds registry from existing published posts
│   ├── web_scraper.py          # Content extraction & analysis
│   ├── update_landing.py       # Landing page & archive updater
│   ├── publish_substack.py     # Substack publishing via API
│   ├── substack_playwright.py  # Cookie extraction helper
│   └── test_web_access.py      # Web access diagnostics
├── prompts/
│   ├── story_system.md         # Story generation system prompt
│   ├── story_model_routing.yaml # Writer model routing rules by brief shape
│   ├── links_judge_system.md   # Hidden-gem scoring prompt for link curation
│   ├── links_system.md         # Links generation system prompt
│   ├── source_lanes.yaml       # Curated lane catalog + theme-specific discovery overrides
│   ├── research_strategy_system.md  # LLM research strategy prompt
│   ├── themes.yaml             # Unified themes for stories + links
│   └── style_modifiers.yaml    # Randomized story constraint pools
├── overrides/
│   ├── home.html               # Custom homepage template
│   └── main.html               # Base template override
├── data/discovery/
│   ├── link_registry.json      # Persistent URL hash registry for hard dedup
│   ├── candidates.jsonl        # Repo-backed discovery corpus
│   ├── selection_history.jsonl # Published-link history for novelty penalties
│   ├── domain_state.json       # Per-domain freshness/frequency tracking
│   └── story_context/          # Selected-link motifs for same-day story inspiration
├── cache/
│   └── web_content/            # Ephemeral scraped-page cache
├── mkdocs.yml                  # MkDocs configuration
└── requirements.txt            # Python dependencies installed into .venv via uv
```

## Workflows

### Daily Content Generation

Runs daily at 6 AM UTC via `.github/workflows/generate-content.yml`:

1. Bootstrap the link registry if needed from existing posts
2. Explore curated source lanes, bounded seed-page crawl, and trusted-domain search; update the discovery corpus, then publish links → `docs/links/posts/`
3. Generate story → `docs/bits/posts/` using selected-link motifs from the same run
4. Update landing pages and archives
5. Commit and push → persists discovery memory and triggers GitHub Pages deploy

#### Manual Orchestration

Use `scripts/run_daily.py` to run all three steps locally with a single command:

```bash
uv run --python .venv/bin/python scripts/run_daily.py
```

**Options**

```bash
# Provide explicit theme JSON (string or path)
uv run --python .venv/bin/python scripts/run_daily.py --theme-json '{"name": "quantum mysteries", "story": "decoder cults", "links": "analog cryptography"}'
uv run --python .venv/bin/python scripts/run_daily.py --theme-json path/to/custom-theme.json

# Pick a specific date from themes.yaml (uses overrides or rotation)
uv run --python .venv/bin/python scripts/run_daily.py --date 2026-02-14

# Skip specific steps if needed
uv run --python .venv/bin/python scripts/run_daily.py --skip-story      # links + landing only
uv run --python .venv/bin/python scripts/run_daily.py --skip-links      # story + landing only
uv run --python .venv/bin/python scripts/run_daily.py --skip-landing    # story + links only

# Pass overrides directly to individual scripts
uv run --python .venv/bin/python scripts/generate_links.py --theme-json '{"name": "lost utilities", "story": "haunted telecom", "links": "abandoned power grids"}'
uv run --python .venv/bin/python scripts/generate_story.py --theme-json custom-theme.json
uv run --python .venv/bin/python scripts/update_landing.py --theme-json custom-theme.json

```

When `--theme-json` is omitted, all scripts fall back to loading `prompts/themes.yaml` (with date overrides). Setting the `THEME_JSON` environment variable has the same effect as passing `--theme-json`.

### Manual Trigger

Actions → "Generate Daily Content" → "Run workflow"

Options:
- Generate story only
- Generate links only
- Generate both

## Edition System

Editions are numbered from launch date (2026-01-30):
- Edition #001 = Jan 30, 2026
- Edition #002 = Jan 31, 2026
- etc.

The `get_edition_number()` function in scripts calculates this.

## Unified Theming System

All daily content shares a cohesive theme through `prompts/themes.yaml`:

### Theme Structure
```yaml
themes:
  - name: "quantum mysteries"
    story: "quantum computing paradoxes"
    links: "quantum physics papers"
  - name: "biological computing"
    story: "DNA-based data storage"
    links: "synthetic biology research"
```

### Theme Selection
- 18 rotating themes (day of year % 18)
- Date-specific overrides for special editions
- Theme included in frontmatter of all content

### Implementation
- `generate_story.py` uses theme's `story` direction
- `generate_links.py` uses theme's `links` direction
- Theme displayed on landing page and archive pages
- Edition snapshots include theme metadata

## Style Modifiers System

Story generation uses randomized style constraints from `prompts/style_modifiers.yaml` to ensure every story feels distinct, even when themes repeat.

### Dimensions

| Dimension | Examples | Count |
|-----------|----------|-------|
| **pov** | First person unreliable, epistolary, stream of consciousness | 15 |
| **tone** | Darkly comic, tense thriller, quiet wonder | 15 |
| **era** | 1970s analog, near future 2040s, post-collapse | 15 |
| **setting** | Small apartment, underground bunker, moving vehicle | 16 |
| **structure** | Reverse chronology, fragmented vignettes, countdown | 14 |
| **conflict** | Person vs. bureaucracy, moral dilemma, uncanny mundane | 14 |
| **opening** | Sensory detail, mid-dialogue, contradiction | 14 |
| **genre** | Cosmic horror, workplace comedy, noir detective, fable | 15 |
| **wildcard** | One lie the reader can catch, no names, one room only | 12 |

With ~15 options per dimension across 9 dimensions + 12 banned word sets, there are **billions of unique combinations**.

### How It Works

1. A **deterministic seed** is derived from the date (`SHA-256(YYYY-MM-DD)`)
2. Each dimension is independently sampled using that seed
3. A **banned word set** is also selected to prevent repetitive language
4. The selected modifiers are injected into the story prompt as strict constraints
5. The **genre** modifier is written to story frontmatter and displayed as a tag on cards

The same date always produces the same style combination, making generation reproducible.

### Story Frontmatter

Generated stories include full metadata:

```yaml
---
date: 2026-02-10
title: "The Duplicate Report"
description: "A daily AI-generated story exploring speculative fiction"
author: "https://integrate.api.nvidia.com/v1 / nvidia/llama-3.3-nemotron-super-49b-v1.5"
theme: "parallel dimensions"
genre: "Fable or parable, simple surface hiding depth"
---
```

The `genre` field is parsed by `update_landing.py` and displayed as a tag on:
- The landing page story card (`.ob-today__genre`)
- Bits archive cards (`.archive-item__genre`)
- Editions archive cards (`.archive-item__genre`)

## Substack Integration

### Architecture

We use a two-script approach to bypass Cloudflare protection:

1. **`substack_playwright.py`** - Extracts cookies via browser automation
2. **`publish_substack.py`** - Uses those cookies for clean API publishing

### Cookie Authentication

The system supports two cookie methods:

**File-based (recommended for local):**
```bash
export SUBSTACK_COOKIES_PATH="$HOME/.substack_cookies.json"
```

**Environment variable (for CI):**
```bash
export SUBSTACK_COOKIES='[{"name":"session", "value":"..."}]'
```

### Publishing Flow

1. Extract cookies once with Playwright (bypasses Cloudflare)
2. Use cookies with Substack API (clean, reliable)
3. API creates draft → prepublish → publish
4. Marker file prevents duplicate publishing

### Published Markers

After publishing, a marker file is created:
```
docs/substack/edition-003-published.txt
```

This prevents duplicate publishing. Use `--force` to override.

## Tech Stack

- **Site**: MkDocs Material
- **AI**: NVIDIA NIM API (Llama 3.3 Nemotron)
- **Hosting**: GitHub Pages
- **CI**: GitHub Actions
- **Substack**: Playwright + API (cookie-based auth)

## See Also

- [SYSTEM_DESIGN.md](SYSTEM_DESIGN.md) - Architecture diagrams and flows
