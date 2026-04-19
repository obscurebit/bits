# Obscure Bit System Design

## Overview

Obscure Bit is a queue-first daily publishing system for stories, curated links, and edition pages.

- Scheduled GitHub Actions runs no longer publish directly from live generation.
- The scheduled path prepares staged content under `data/edition_queue/<date>/` first, then promotes that prepared edition into `docs/`.
- `scripts/run_daily.py` still exists as the single-date orchestrator, but it is now mainly the engine behind queue preparation and manual escape-hatch runs.
- Link discovery remains lane-first and repo-memory-backed.
- Story generation remains deterministic by date through the style-modifier system.
- `scripts/publish_substack.py` is still part of the repo, but CI only uses it to generate newsletter markdown/history unless explicitly run in draft/publish mode.

## Architecture

```mermaid
graph TB
    subgraph "GitHub Actions (schedule)"
        A[prepare_queue.py] --> B[publish_prepared.py]
        B --> C[publish_substack.py<br/>markdown/history only]
        C --> D[Commit and Push]
    end

    subgraph "GitHub Actions (workflow_dispatch)"
        E[run_daily.py] --> F[update_landing.py]
        F --> D
    end

    subgraph "Generation Core"
        G[generate_links.py]
        H[generate_story.py]
        I[update_landing.py]
    end

    subgraph "Persistent State"
        J[data/discovery]
        K[data/edition_queue]
        L[docs/]
    end

    subgraph "External Inputs"
        M[OpenAI-compatible API]
        N[prompts/themes.yaml]
        O[prompts/source_lanes.yaml]
        P[prompts/style_modifiers.yaml]
    end

    A --> G
    A --> H
    A --> K
    B --> I
    B --> L
    G --> J
    H --> K
    M --> G
    M --> H
    N --> A
    O --> G
    P --> H
```

## Data Flow

```mermaid
flowchart LR
    subgraph "Inputs"
        A1[themes.yaml]
        A2[source_lanes.yaml]
        A3[style_modifiers.yaml]
        A4[OpenAI-compatible API]
    end

    subgraph "Queue Prep"
        B1[prepare_queue.py]
        B2[run_daily.py with OBSCUREBIT_OUTPUT_ROOT]
        B3[generate_links.py]
        B4[generate_story.py]
    end

    subgraph "Staging"
        C1[data/edition_queue date docs bits posts]
        C2[data/edition_queue date docs links posts]
        C3[data/edition_queue date manifest.json]
    end

    subgraph "Promotion"
        D1[publish_prepared.py]
        D2[update_landing.py helpers]
    end

    subgraph "Published Output"
        E1[docs/bits/posts]
        E2[docs/links/posts]
        E3[docs/editions/posts]
        E4[docs/bits/index.md]
        E5[docs/links/index.md]
        E6[docs/editions.md]
        E7[overrides/home.html]
    end

    subgraph "Repo Memory"
        F1[data/discovery/link_registry.json]
        F2[data/discovery/candidates.jsonl]
        F3[data/discovery/selection_history.jsonl]
        F4[data/discovery/domain_state.json]
        F5[data/discovery/story_context]
    end

    A1 --> B1
    A1 --> B2
    A2 --> B3
    A3 --> B4
    A4 --> B3
    A4 --> B4
    B1 --> B2
    B2 --> B3
    B2 --> B4
    B3 --> C2
    B4 --> C1
    B1 --> C3
    B3 --> F1
    B3 --> F2
    B3 --> F3
    B3 --> F4
    B3 --> F5
    C1 --> D1
    C2 --> D1
    C3 --> D1
    D1 --> D2
    D1 --> E1
    D1 --> E2
    D2 --> E3
    D2 --> E4
    D2 --> E5
    D2 --> E6
    D2 --> E7
```

## Queue-First Publish Model

The main reliability change is that daily publishing is no longer supposed to depend on live discovery succeeding in the same step that updates the public site.

### Scheduled Path

1. `scripts/prepare_queue.py` selects one or more target dates.
2. For each date it runs `scripts/run_daily.py` with `OBSCUREBIT_OUTPUT_ROOT` pointed at `data/edition_queue/<date>/`.
3. `run_daily.py` generates links first; if link generation fails, it can fall forward through multiple rotating themes for that date.
4. When links succeed, the same theme is reused for story generation so the edition stays internally consistent.
5. `scripts/publish_prepared.py` copies the staged story and links into `docs/`, rebuilds indexes and edition snapshot pages, and updates the queue manifest to `published`.

### Manual Escape Hatches

- `workflow_dispatch` still runs `run_daily.py` directly for one-off manual generation.
- Developers can still run `generate_story.py`, `generate_links.py`, or `update_landing.py` individually.
- Backfills should now prefer `prepare_queue.py --date ... --force` followed by `publish_prepared.py --date ...`.

## Story Generation and Style Modifiers

Story generation is driven by `scripts/generate_story.py`.

### Deterministic Daily Seed

- A seed is derived from `SHA-256(YYYY-MM-DD)` in `get_daily_seed()`.
- The same date always produces the same style modifier combination.
- That makes backfills reproducible as long as prompts and models are unchanged.

### Current Modifier Shape

The style system currently samples from 14 dimensions plus one banned-word set, not the older 9-dimension version.

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

That yields roughly `1.3824e15` possible combinations before model variation.

### What the Modifiers Affect

- Prompt voice and structure
- Character role and social pressure
- Desired outcome and ending shape
- One explicit `genre` label
- A banned-word set that discourages repetitive speculative vocabulary

### Genre Propagation

The chosen `genre` is written into story frontmatter by `generate_story.py`, then read by `update_landing.py` to decorate:

- the homepage feature card
- bits archive cards
- edition pages and edition archive cards

### Candidate Selection and Routing

`generate_story.py` also supports:

- `STORY_CANDIDATES` for multiple draft generation and selection
- `STORY_MODEL_ROUTING` and `prompts/story_model_routing.yaml` for brief-based writer-model routing
- `STORY_SELECTOR_MODEL` for an optional separate selector model

Queue prep intentionally overrides some of those defaults for speed and reliability:

- `STORY_CANDIDATES=1`
- `STORY_MODEL_ROUTING=0`
- `OPENAI_REQUEST_TIMEOUT=90`

## Link Generation Architecture

The link system remains lane-first and repo-memory-backed.

```mermaid
flowchart LR
    subgraph "Planning"
        A[themes.yaml]
        B[source_lanes.yaml]
        C[theme lane plan]
    end

    subgraph "Discovery"
        D[curated seed URLs]
        E[trusted seed domains]
        F[seed queries]
        G[bounded seed crawl]
        H[Marginalia plus constrained fallback]
    end

    subgraph "Filtering and Scoring"
        I[link_registry.py]
        J[web_scraper.py]
        K[theme validation]
        L[LLM judge or heuristic]
        M[novelty and diversity selection]
    end

    subgraph "Persistence"
        N[links post]
        O[link_registry.json]
        P[candidates.jsonl]
        Q[selection_history.jsonl]
        R[domain_state.json]
        S[story_context date links.json]
    end

    A --> C
    B --> C
    C --> D
    C --> E
    C --> F
    D --> G
    E --> H
    F --> H
    G --> I
    H --> I
    I --> J
    J --> K
    K --> L
    L --> M
    M --> N
    M --> O
    M --> P
    M --> Q
    M --> R
    M --> S
```

### Link-System Properties

- Discovery starts from curated seeds and trusted domains rather than broad search fanout.
- A persistent URL registry prevents re-publishing the same normalized URL.
- The discovery corpus stores candidates and domain freshness across days.
- `story_context/<date>-links.json` exports same-day motifs into story generation.
- Fallback thresholds exist, but only after stricter theme and quality filters run first.

## Action Flows

### 1. Scheduled Daily Run

```mermaid
sequenceDiagram
    participant GA as GitHub Actions
    participant PQ as prepare_queue.py
    participant RD as run_daily.py
    participant PP as publish_prepared.py
    participant GH as GitHub Repo

    GA->>PQ: scheduled run
    PQ->>RD: build staged edition for target date
    RD->>GH: write staged story and links under data/edition_queue
    PQ->>GH: write queue manifest
    GA->>PP: publish today's prepared edition
    PP->>GH: copy staged files into docs/
    PP->>GH: rebuild indexes, home, edition snapshot
    GA->>GH: commit and push
```

### 2. Manual Backfill

```mermaid
sequenceDiagram
    participant Dev as Developer
    participant PQ as prepare_queue.py
    participant RD as run_daily.py
    participant PP as publish_prepared.py

    Dev->>PQ: --date YYYY-MM-DD --force
    PQ->>RD: generate staged story and links
    PQ-->>Dev: manifest status prepared or failed
    Dev->>PP: --date YYYY-MM-DD
    PP-->>Dev: published docs plus rebuilt indexes
```

### 3. Manual Direct Run

Use this when you explicitly want to bypass the queue:

```bash
uv run --python .venv/bin/python scripts/run_daily.py --date 2026-04-19
```

`run_daily.py` now has:

- idempotent skip behavior if story or links already exist
- bounded per-step timeouts
- multi-theme fallback for links when no explicit `--theme-json` is supplied

### 4. Substack Workflow

- CI runs `scripts/publish_substack.py` with no publish flags, which is used for newsletter markdown/history generation.
- Actual Substack draft/publish actions still require running `publish_substack.py --draft` or `--publish`.
- `scripts/substack_playwright.py` remains the helper for extracting cookies when API auth via browser session is needed.

## File Structure

```text
b1ts/
├── .github/workflows/
│   └── generate-content.yml
├── docs/
│   ├── bits/posts/
│   ├── links/posts/
│   ├── editions/posts/
│   ├── bits/index.md
│   ├── links/index.md
│   ├── editions.md
│   └── substack/
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
│   │   ├── link_registry.json
│   │   ├── candidates.jsonl
│   │   ├── selection_history.jsonl
│   │   ├── domain_state.json
│   │   └── story_context/
│   └── edition_queue/
│       └── YYYY-MM-DD/
│           ├── docs/bits/posts/
│           ├── docs/links/posts/
│           └── manifest.json
└── cache/
    └── web_content/
```

## Environment Variables

### Core Model Settings

```bash
OPENAI_API_KEY
OPENAI_API_BASE
OPENAI_MODEL
STORY_SELECTOR_MODEL
STORY_MODEL_ROUTING
STORY_CANDIDATES
OPENAI_REQUEST_TIMEOUT
OPENAI_MAX_RETRIES
```

### Orchestrator and Queue Controls

```bash
AUTO_THEME_ATTEMPTS
RUN_DAILY_LINK_TIMEOUT_SECONDS
RUN_DAILY_STORY_TIMEOUT_SECONDS
RUN_DAILY_LANDING_TIMEOUT_SECONDS
OBSCUREBIT_OUTPUT_ROOT
```

### Substack

Used only when running Substack draft/publish flows:

```bash
SUBSTACK_PUBLICATION_URL
SUBSTACK_EMAIL
SUBSTACK_PASSWORD
SUBSTACK_COOKIES
SUBSTACK_COOKIES_PATH
```

## Error Handling

### Queue Prep Failures

- `prepare_queue.py` records `failed` manifests when staging does not complete.
- The scheduled workflow currently marks queue prep `continue-on-error: true`, so a publish step can still run if today's prepared edition already exists.

### Direct Run Failures

- `run_daily.py` applies explicit timeouts to link, story, and landing steps.
- Link generation can retry across multiple rotating themes for the target date.
- Explicit `--theme-json` runs do not silently fall to another theme.

### Link Discovery Failures

- Curated seeds, trusted domains, and repo-backed discovery memory reduce dependence on live search luck.
- The registry and corpus are committed so selection state persists across runs.

### Substack Failures

- Cloudflare and auth issues still make true publish automation brittle.
- Markdown/history generation is safe in CI.
- Draft/publish actions should still be treated as operator-driven.

## Monitoring

- GitHub Actions run status
- Queue manifests under `data/edition_queue/<date>/manifest.json`
- Published docs pages and indexes
- Discovery corpus changes under `data/discovery/`
