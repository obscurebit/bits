# Development Guide

Technical notes for working on Obscure Bit locally.

## Quick Start

```bash
git clone https://github.com/obscurebit/b1ts.git
cd b1ts
uv venv
uv pip install --python .venv/bin/python -r requirements.txt
```

Use `uv run --python .venv/bin/python ...` for project scripts. Do not rely on your shell Python.

## Local Site Work

### Serve Locally

```bash
uv run --python .venv/bin/python mkdocs serve
```

Site: `http://127.0.0.1:8000`

### Build the Site

```bash
uv run --python .venv/bin/python mkdocs build
python3 -m http.server 8000 --directory site
```

## Environment

### Core Generation Variables

```bash
export OPENAI_API_KEY="..."
export OPENAI_API_BASE="https://integrate.api.nvidia.com/v1"
export OPENAI_MODEL="nvidia/llama-3.3-nemotron-super-49b-v1.5"
export STORY_SELECTOR_MODEL="$OPENAI_MODEL"
export STORY_MODEL_ROUTING="1"
export STORY_CANDIDATES="2"
export OPENAI_REQUEST_TIMEOUT="120"
export OPENAI_MAX_RETRIES="2"
```

### Optional Orchestrator Controls

```bash
export AUTO_THEME_ATTEMPTS="8"
export RUN_DAILY_LINK_TIMEOUT_SECONDS="900"
export RUN_DAILY_STORY_TIMEOUT_SECONDS="420"
export RUN_DAILY_LANDING_TIMEOUT_SECONDS="180"
```

### Substack Variables

Only needed when using draft/publish flows:

```bash
export SUBSTACK_PUBLICATION_URL="https://obscurebit.substack.com"
export SUBSTACK_COOKIES_PATH="$HOME/.substack_cookies.json"
```

## GitHub Actions Model

There are now two distinct operational paths:

### Scheduled Runs

The scheduled workflow is queue-first:

1. `scripts/backfill_registry.py`
2. `scripts/prepare_queue.py`
3. `scripts/publish_prepared.py --update-home`
4. `scripts/publish_substack.py` for newsletter markdown/history generation
5. commit and push

Scheduled publishing should not depend on updating the public site directly from live discovery anymore.

### Manual Runs

`workflow_dispatch` still uses `scripts/run_daily.py` directly as a manual escape hatch. It supports:

- `theme_json`
- `theme_date`
- `generate_story`
- `generate_links`
- `update_landing`

## Core Scripts

### Queue and Publish

```bash
# Prepare the default queue window starting today
uv run --python .venv/bin/python scripts/prepare_queue.py

# Prepare one specific date
uv run --python .venv/bin/python scripts/prepare_queue.py --date 2026-04-19

# Force a rebuild for a date
uv run --python .venv/bin/python scripts/prepare_queue.py --date 2026-04-19 --force

# Publish a prepared date into docs/
uv run --python .venv/bin/python scripts/publish_prepared.py --date 2026-04-19

# Publish today's prepared date and refresh home page
uv run --python .venv/bin/python scripts/publish_prepared.py --date 2026-04-19 --update-home
```

### Direct Orchestration

```bash
# Direct full run
uv run --python .venv/bin/python scripts/run_daily.py

# Direct run for a specific date
uv run --python .venv/bin/python scripts/run_daily.py --date 2026-04-19

# Skip individual steps
uv run --python .venv/bin/python scripts/run_daily.py --skip-links
uv run --python .venv/bin/python scripts/run_daily.py --skip-story
uv run --python .venv/bin/python scripts/run_daily.py --skip-landing

# Use an explicit theme
uv run --python .venv/bin/python scripts/run_daily.py --theme-json '{"name":"municipal weirdness","story":"...","links":"..."}'
uv run --python .venv/bin/python scripts/run_daily.py --theme-json path/to/theme.json
```

`run_daily.py` behavior to remember:

- links run first
- if links fail and no explicit theme was supplied, the script can fall through multiple rotating themes
- once links succeed, the successful theme is reused for story and landing
- if story or links already exist for the target date, the script skips regenerating that step

### Individual Generators

```bash
uv run --python .venv/bin/python scripts/generate_links.py --date 2026-04-19
uv run --python .venv/bin/python scripts/generate_story.py --date 2026-04-19
uv run --python .venv/bin/python scripts/update_landing.py --date 2026-04-19
```

### Staged Output Root

Queue prep works by setting `OBSCUREBIT_OUTPUT_ROOT` so generators write into a staging tree instead of `docs/`.

```bash
OBSCUREBIT_OUTPUT_ROOT=data/edition_queue/2026-04-19 \
uv run --python .venv/bin/python scripts/run_daily.py --date 2026-04-19 --skip-landing
```

That path is normally handled for you by `prepare_queue.py`.

## Backfills

Prefer the queue-first path for backfills:

```bash
uv run --python .venv/bin/python scripts/prepare_queue.py --date 2026-04-17 --force
uv run --python .venv/bin/python scripts/publish_prepared.py --date 2026-04-17
```

Use direct `run_daily.py --date ...` only when you intentionally want to bypass staging.

## Discovery Memory

The link system persists cross-run memory under `data/discovery/`.

Files:

- `link_registry.json`: hard dedup for published URLs
- `candidates.jsonl`: compact scored candidates
- `selection_history.jsonl`: novelty-aware selection history
- `domain_state.json`: freshness/frequency tracking
- `story_context/<date>-links.json`: same-day motifs for story generation

Bootstrap command:

```bash
uv run --python .venv/bin/python scripts/backfill_registry.py
```

This still exists as a safety bootstrap in CI.

## Style Modifier System

Story generation uses `prompts/style_modifiers.yaml` and a deterministic seed from `SHA-256(YYYY-MM-DD)`.

### Current Dimensions

The current system samples 14 dimensions plus a banned-word set:

| Dimension | Count |
|-----------|-------|
| `pov` | 10 |
| `tone` | 10 |
| `era` | 10 |
| `setting` | 12 |
| `structure` | 10 |
| `conflict` | 10 |
| `opening` | 10 |
| `genre` | 10 |
| `wildcard` | 10 |
| `protagonist` | 12 |
| `desire` | 10 |
| `anchor_object` | 12 |
| `social_pressure` | 10 |
| `ending_shape` | 10 |
| `banned_word_sets` | 8 |

This is the live system in `scripts/generate_story.py`. Older docs that describe 9 dimensions are stale.

### What Happens at Runtime

1. `generate_story.py` derives a deterministic date seed.
2. One option is sampled from each modifier pool.
3. One banned-word set is sampled.
4. The assembled brief is injected into the story prompt.
5. The chosen `genre` is written to frontmatter.
6. `update_landing.py` surfaces that genre on the homepage and archive cards.

### Model Routing and Candidate Selection

`generate_story.py` also supports:

- `prompts/story_model_routing.yaml`
- `STORY_MODEL_ROUTING`
- `STORY_CANDIDATES`
- `STORY_SELECTOR_MODEL`

Queue prep deliberately uses more conservative defaults:

- `STORY_CANDIDATES=1`
- `STORY_MODEL_ROUTING=0`
- `OPENAI_REQUEST_TIMEOUT=90`

That keeps scheduled staging cheaper and more predictable.

## Unified Theme System

All daily content shares a theme from `prompts/themes.yaml`.

Each theme has:

```yaml
name: "municipal weirdness"
story: "story-facing direction"
links: "link-facing direction"
```

Selection behavior:

- date-specific override if present
- otherwise rotation based on day-of-year
- for direct runs without explicit theme JSON, link generation may fall forward through additional rotating themes when the first theme does not yield enough viable links

## Substack

### What CI Does

The scheduled workflow currently runs:

```bash
uv run --python .venv/bin/python scripts/publish_substack.py
```

With no `--draft` or `--publish` flag, this is for newsletter markdown/history generation, not automatic live publishing.

### Local Draft or Publish

```bash
# Install Playwright if needed
uv pip install --python .venv/bin/python playwright
uv run --python .venv/bin/python playwright install chromium

# One-time cookie setup
uv run --python .venv/bin/python scripts/substack_playwright.py --login

# Create draft
uv run --python .venv/bin/python scripts/publish_substack.py --edition 3 --draft

# Publish
uv run --python .venv/bin/python scripts/publish_substack.py --edition 3 --publish
```

## Project Structure

```text
b1ts/
├── .github/workflows/
│   ├── deploy.yml
│   └── generate-content.yml
├── docs/
│   ├── bits/posts/
│   ├── links/posts/
│   ├── editions/posts/
│   ├── substack/
│   └── ...
├── scripts/
│   ├── run_daily.py
│   ├── generate_story.py
│   ├── generate_links.py
│   ├── prepare_queue.py
│   ├── publish_prepared.py
│   ├── project_paths.py
│   ├── discovery_corpus.py
│   ├── link_registry.py
│   ├── backfill_registry.py
│   ├── update_landing.py
│   ├── publish_substack.py
│   └── substack_playwright.py
├── prompts/
│   ├── themes.yaml
│   ├── source_lanes.yaml
│   ├── style_modifiers.yaml
│   ├── story_model_routing.yaml
│   └── ...
├── data/
│   ├── discovery/
│   └── edition_queue/
├── overrides/
├── cache/
├── mkdocs.yml
└── requirements.txt
```

## Verification

Useful checks after code changes:

```bash
./.venv/bin/python -m py_compile scripts/*.py tests/*.py
./.venv/bin/python -m unittest tests/test_prepare_queue.py tests/test_run_daily.py tests/test_publish_prepared.py
```

For content-path changes, also test one queued date locally:

```bash
uv run --python .venv/bin/python scripts/prepare_queue.py --date 2026-04-19 --force
uv run --python .venv/bin/python scripts/publish_prepared.py --date 2026-04-19
```
