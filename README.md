# Obscure Bit

A daily publication of AI-generated speculative stories and curated links from obscure corners of the web.

🌐 **[obscurebit.com](https://obscurebit.com)**

## What It Produces

Every day Obscure Bit publishes:

- **Daily Bits**: one story
- **Obscure Links**: one curated links post
- **Daily Edition**: a combined edition snapshot

## How It Works

Obscure Bit now uses a queue-first publishing model.

```text
GitHub Actions schedule
    ↓
prepare_queue.py stages one or more dated editions
    ↓
publish_prepared.py promotes today's prepared edition into docs/
    ↓
MkDocs site updates and GitHub Pages deploys
```

That means the public site is no longer supposed to depend on live discovery succeeding in the same step that publishes the day’s content.

### Scheduled Path

- `scripts/backfill_registry.py` bootstraps link-memory state if needed
- `scripts/prepare_queue.py` builds staged content under `data/edition_queue/<date>/`
- `scripts/publish_prepared.py --update-home` publishes today’s prepared edition
- `scripts/publish_substack.py` generates newsletter markdown/history
- GitHub Actions commits and pushes the resulting changes

### Manual Path

- `workflow_dispatch` still runs `scripts/run_daily.py` directly as a one-off escape hatch
- Developers can also run `generate_story.py`, `generate_links.py`, and `update_landing.py` individually
- Backfills should usually use `prepare_queue.py` followed by `publish_prepared.py`

## Quick Start

```bash
git clone https://github.com/obscurebit/b1ts.git
cd b1ts
uv venv
uv pip install --python .venv/bin/python -r requirements.txt
uv run --python .venv/bin/python mkdocs serve
```

## Common Commands

```bash
# Prepare staged content for today and the next few dates
uv run --python .venv/bin/python scripts/prepare_queue.py

# Prepare a specific date
uv run --python .venv/bin/python scripts/prepare_queue.py --date 2026-04-19 --force

# Publish a prepared date
uv run --python .venv/bin/python scripts/publish_prepared.py --date 2026-04-19

# Direct manual run
uv run --python .venv/bin/python scripts/run_daily.py --date 2026-04-19

# Draft the 256 Bits book production artifacts locally
uv run --python .venv/bin/python scripts/book_build.py --allow-incomplete
```

## 256 Bits Book

The repo includes a local-only production scaffold for `256 Bits: Volume 1`, a curated 16 by 16 selection indexed `00` through `FF`.

- Source controls live under `book/volume-1/`
- Generated book artifacts live under ignored `book-output/volume-1/`
- Private scans, final art, and rights reports live under ignored `private/`
- The release build intentionally fails while the volume is incomplete or any validation blocker remains

See [book/volume-1/PRODUCTION_PLAN.md](book/volume-1/PRODUCTION_PLAN.md).

## Story and Link Systems

- Story generation uses deterministic date-seeded style modifiers from `prompts/style_modifiers.yaml`
- Link generation is lane-first and repo-memory-backed using `prompts/source_lanes.yaml` and `data/discovery/`
- `run_daily.py` can fall forward through multiple rotating themes when link generation fails for the first candidate theme

## Substack

The repo still contains Substack tooling:

- `scripts/publish_substack.py`
- `scripts/substack_playwright.py`

In practice:

- CI currently uses `publish_substack.py` for newsletter markdown/history generation
- actual draft/publish actions remain explicit operator actions
- cookie/browser-based auth may still be needed because Substack automation is brittle

## Documentation

- [DEVELOPMENT.md](DEVELOPMENT.md): setup, commands, workflows, backfills, style modifiers
- [SYSTEM_DESIGN.md](SYSTEM_DESIGN.md): architecture, queue model, data flow, failure handling

## Tech Stack

- **Site**: MkDocs Material
- **Generation**: OpenAI-compatible API, currently NVIDIA NIM by default
- **Hosting**: GitHub Pages
- **Automation**: GitHub Actions

## License

MIT
