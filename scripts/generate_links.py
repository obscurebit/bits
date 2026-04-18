#!/usr/bin/env python3
"""
Generate daily obscure links for Obscure Bit - Version 2.

This script implements an improved link generation pipeline:
1. Search the web for candidate URLs based on theme
2. Scrape content from each URL
3. Verify relevance to theme using LLM analysis
4. Score based on relevance + obscurity
5. Return curated list of best links
"""

import os
import sys
import re
import json
import argparse
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from urllib.parse import quote_plus, urlparse, parse_qs, unquote, urlunparse
import random
import time
import subprocess

import yaml
import requests
from bs4 import BeautifulSoup
try:
    import certifi
except Exception:
    certifi = None

try:
    from openai import OpenAI
    OPENAI_IMPORT_ERROR = None
except Exception as exc:
    OpenAI = None
    OPENAI_IMPORT_ERROR = exc

# Import our web scraper
from web_scraper import WebScraper, ScrapedContent
from link_registry import LinkRegistry, normalize_url
from discovery_corpus import DiscoveryCorpus, STORY_CONTEXT_DIR, classify_source_lane
from project_paths import links_posts_output_dir

# Configuration
API_BASE = os.environ.get("OPENAI_API_BASE", "https://integrate.api.nvidia.com/v1")
API_KEY = os.environ.get("OPENAI_API_KEY")
MODEL = os.environ.get("OPENAI_MODEL", "nvidia/llama-3.3-nemotron-super-49b-v1.5")
OPENAI_REQUEST_TIMEOUT = max(30, int(os.environ.get("OPENAI_REQUEST_TIMEOUT", "120")))
OPENAI_MAX_RETRIES = max(0, int(os.environ.get("OPENAI_MAX_RETRIES", "2")))

# Paths
PROMPTS_DIR = Path(__file__).parent.parent / "prompts"
SYSTEM_PROMPT_FILE = PROMPTS_DIR / "links_system.md"
LINK_JUDGE_PROMPT_FILE = PROMPTS_DIR / "links_judge_system.md"
SOURCE_LANES_FILE = PROMPTS_DIR / "source_lanes.yaml"
THEMES_FILE = PROMPTS_DIR / "themes.yaml"
CACHE_DIR = Path("cache/link_generation")
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Search configuration
SEARCH_TIMEOUT = 15
REQUEST_MAX_RETRIES = 3
REQUEST_RETRYABLE_STATUSES = {408, 425, 429, 500, 502, 503, 504}
NETWORK_DISABLE_AFTER_FAILURES = 4
MAX_CANDIDATES = 40
MIN_RELEVANCE_SCORE = 0.45
MIN_OBSCURITY_SCORE = 0.3
MIN_GEM_SCORE = 0.55
MIN_RELEVANCE_FALLBACK = 0.35
MIN_OBSCURITY_FALLBACK = 0.25
MINIMUM_SELECTED_LINKS = 3
MIN_LLM_DOMAIN_IDEAS = 3
MIN_LLM_SEARCH_QUERIES = 3

DDG_MAX_RETRIES = 1
DDG_MIN_INTERVAL = 1.0  # seconds between requests
DDG_THROTTLE_FACTOR = 1.5
DDG_DISABLE_AFTER_FAILURES = 1  # disable after first failure — DDG throttles CI hard
DDG_MAX_QUERIES_PER_RUN = 1  # single broad query to avoid 202 storms
DDG_USER_AGENTS = [
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Gecko/20100101 Firefox/121.0'
]

DEFAULT_SOURCE_LANE_ORDER = [
    "primary-doc",
    "enthusiast-research",
    "old-web",
    "museum-object",
    "local-history",
    "niche-institution",
    "indie-essay",
]

DEFAULT_SOURCE_LANES = {
    "primary-doc": {
        "seed_domains": [
            "bitsavers.org",
            "textfiles.com",
            "cryptomuseum.com",
            "codesandciphers.org.uk",
        ],
        "query_templates": [
            '"{theme_name}" "{primary_term}" "manual" -wikipedia -github',
            '"{theme_name}" "{primary_term}" "field report" -shop -store',
            '"{theme_name}" "{links_direction}" "lab notes" -site:medium.com',
        ],
    },
    "enthusiast-research": {
        "seed_domains": [
            "vcfed.org",
            "nycsubway.org",
            "subbrit.org.uk",
            "lowtechmagazine.com",
        ],
        "query_templates": [
            '"{theme_name}" "{links_direction}" "independent research"',
            '"{theme_name}" "{primary_term}" "collector" OR "restoration"',
            '"{theme_name}" "{links_direction}" "field notes" -wikipedia',
        ],
    },
    "old-web": {
        "seed_domains": [
            "textfiles.com",
            "404pagefound.com",
            "lostmediawiki.com",
        ],
        "query_templates": [
            '"{theme_name}" "{links_direction}" "personal site"',
            '"{theme_name}" "{primary_term}" "webring" OR "BBS"',
            '"{theme_name}" "{links_direction}" "homepage" -site:github.com',
        ],
    },
    "museum-object": {
        "seed_domains": [
            "wellcomecollection.org",
            "collection.sciencemuseumgroup.org.uk",
            "si.edu",
            "computerhistory.org",
        ],
        "query_templates": [
            '"{theme_name}" "{links_direction}" "museum object" -shop -store',
            '"{theme_name}" "{primary_term}" "collection item"',
            '"{theme_name}" "{links_direction}" "object record"',
        ],
    },
    "local-history": {
        "seed_domains": [
            "nycsubway.org",
            "abandonedstations.org.uk",
            "subbrit.org.uk",
            "roadsideamerica.com",
        ],
        "query_templates": [
            '"{theme_name}" "{links_direction}" "local history"',
            '"{theme_name}" "{primary_term}" "historical society"',
            '"{theme_name}" "{links_direction}" "oral history"',
        ],
    },
    "niche-institution": {
        "seed_domains": [
            "publicdomainreview.org",
            "wellcomecollection.org",
            "computerhistory.org",
            "cabinetmagazine.org",
        ],
        "query_templates": [
            '"{theme_name}" "{links_direction}" "archive"',
            '"{theme_name}" "{primary_term}" "case study" -site:.edu',
            '"{theme_name}" "{links_direction}" "catalog"',
        ],
    },
    "indie-essay": {
        "seed_domains": [
            "cabinetmagazine.org",
            "lowtechmagazine.com",
            "publicdomainreview.org",
        ],
        "query_templates": [
            '"{theme_name}" "{links_direction}" essay',
            '"{theme_name}" "{primary_term}" "notes"',
            '"{theme_name}" "{links_direction}" "forgotten history"',
        ],
    },
}

THINKING_BLOCK_RE = re.compile(r'^<think>.*?</think>\s*', re.S)
DISALLOWED_BASE_DOMAINS = ("wikipedia.org", "archive.org", "github.com")

# Domains that produce junk / irrelevant content — block at candidate stage
DISALLOWED_LINK_DOMAINS = {
    "listverse.com", "buzzfeed.com", "boredpanda.com", "ranker.com",
    "list25.com", "viralnova.com", "thecoolist.com", "therichest.com",
    "softwareheritage.org", "packsify.com", "techaro.lol",
    "newworldencyclopedia.org", "encyclopedia.com", "zxc.wiki",
    "reddit.com", "www.reddit.com", "stackexchange.com", "worldbuilding.stackexchange.com",
    "medium.com", "substack.com", "linkedin.com", "facebook.com", "x.com", "twitter.com",
    "youtube.com", "www.youtube.com", "bsky.app", "mastodon.social", "creativecommons.org",
    "oclc.org", "policies.oclc.org", "help.oclc.org", "lite.ip2location.com",
    "about.marginalia-search.com",
    "sufficientvelocity.com", "forums.sufficientvelocity.com",
}


_last_ddg_request_time = 0.0
_ddg_failure_count = 0
_ddg_disabled_for_run = False
_ddg_queries_this_run = 0
_network_failure_count = 0
_network_disabled_for_run = False


def get_requests_verify():
    """Return a CA bundle path when available so HTTPS failures are less environment-sensitive."""
    if certifi:
        try:
            bundle = certifi.where()
            if bundle and Path(bundle).exists():
                return bundle
        except Exception:
            pass
    return True


def build_requests_session(headers: Optional[Dict[str, str]] = None) -> requests.Session:
    session = requests.Session()
    session.verify = get_requests_verify()
    if headers:
        session.headers.update(headers)
    return session


def _is_transient_request_error(error: Exception) -> bool:
    message = str(error).lower()
    markers = (
        "name or service not known",
        "temporary failure in name resolution",
        "failed to resolve",
        "name resolution",
        "nodename nor servname provided",
        "getaddrinfo failed",
        "connection reset",
        "connection aborted",
        "connection refused",
        "timed out",
        "timeout",
        "temporarily unavailable",
        "remote disconnected",
        "eai_again",
    )
    return any(marker in message for marker in markers)


def _request_variants(url: str) -> List[str]:
    variants = [url]
    try:
        parsed = urlparse(url)
    except Exception:
        return variants

    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return variants

    host = parsed.hostname or ""
    if not host:
        return variants

    alternate_host = None
    if host.startswith("www."):
        alternate_host = host[4:]
    elif host.count(".") == 1:
        alternate_host = f"www.{host}"

    if not alternate_host or alternate_host == host:
        return variants

    netloc = alternate_host
    if parsed.port:
        netloc = f"{alternate_host}:{parsed.port}"
    alternate = urlunparse(parsed._replace(netloc=netloc))
    if alternate not in variants:
        variants.append(alternate)
    return variants


def request_with_retries(
    session: requests.Session,
    method: str,
    url: str,
    *,
    attempts: int = REQUEST_MAX_RETRIES,
    retry_statuses: Optional[set] = None,
    retry_variants: bool = True,
    retry_label: str = "request",
    **kwargs,
):
    global _network_disabled_for_run
    retry_statuses = retry_statuses or REQUEST_RETRYABLE_STATUSES
    variants = _request_variants(url) if retry_variants else [url]
    last_error: Optional[Exception] = None

    if _network_disabled_for_run:
        raise requests.RequestException("fresh web discovery disabled for this run after repeated transient network failures")

    for attempt in range(attempts):
        for candidate_url in variants:
            try:
                response = session.request(method, candidate_url, **kwargs)
                if response.status_code in retry_statuses:
                    print(
                        f"      ⚠️  {retry_label} got status {response.status_code} "
                        f"for {candidate_url[:60]}... (attempt {attempt + 1}/{attempts})"
                    )
                    last_error = requests.HTTPError(f"retryable status {response.status_code}")
                    continue
                _record_network_success()
                return response
            except requests.RequestException as error:
                last_error = error
                if not _is_transient_request_error(error):
                    raise
                print(
                    f"      ⚠️  {retry_label} transient failure for {candidate_url[:60]}...: {error} "
                    f"(attempt {attempt + 1}/{attempts})"
                )

        if attempt < attempts - 1:
            time.sleep(1.5 * (attempt + 1))

    if last_error:
        if _is_transient_request_error(last_error):
            _record_network_failure()
        raise last_error
    raise requests.RequestException(f"{retry_label} failed for {url}")


def _throttle_ddg(min_interval: float) -> None:
    """Ensure we respect a minimum interval between DuckDuckGo requests."""
    global _last_ddg_request_time
    now = time.time()
    wait = min_interval - (now - _last_ddg_request_time)
    if wait > 0:
        time.sleep(wait)
    _last_ddg_request_time = time.time()


def _reset_ddg_state() -> None:
    global _ddg_failure_count, _ddg_disabled_for_run
    _ddg_failure_count = 0
    _ddg_disabled_for_run = False


def _record_ddg_success() -> None:
    global _ddg_failure_count
    _ddg_failure_count = 0


def _record_ddg_failure() -> None:
    global _ddg_failure_count, _ddg_disabled_for_run
    _ddg_failure_count += 1
    if not _ddg_disabled_for_run and _ddg_failure_count >= DDG_DISABLE_AFTER_FAILURES:
        _ddg_disabled_for_run = True
        print("      ⚠️  Disabling DuckDuckGo for the remainder of this run")


def _record_network_success() -> None:
    global _network_failure_count
    _network_failure_count = 0


def _record_network_failure() -> None:
    global _network_failure_count, _network_disabled_for_run
    _network_failure_count += 1
    if not _network_disabled_for_run and _network_failure_count >= NETWORK_DISABLE_AFTER_FAILURES:
        _network_disabled_for_run = True
        print("      ⚠️  Disabling fresh web discovery for the remainder of this run")


def load_system_prompt() -> str:
    """Load the system prompt from external file."""
    if not SYSTEM_PROMPT_FILE.exists():
        return "You are a helpful assistant that finds obscure and interesting web links."
    return SYSTEM_PROMPT_FILE.read_text().strip()


def load_link_judge_prompt() -> str:
    """Load the structured link judge prompt."""
    if not LINK_JUDGE_PROMPT_FILE.exists():
        return "You score links for thematic fit and hidden-gem quality. Return JSON only."
    return LINK_JUDGE_PROMPT_FILE.read_text().strip()


def load_source_lanes() -> dict:
    """Load curated source lanes and theme-specific seeds."""
    if not SOURCE_LANES_FILE.exists():
        return {}
    return yaml.safe_load(SOURCE_LANES_FILE.read_text()) or {}


RESEARCH_STRATEGY_PROMPT_FILE = PROMPTS_DIR / "research_strategy_system.md"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate curated obscure links")
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
        print(f"⚠️  Failed to parse theme override: {e}")
        return None

def load_research_strategy_prompt() -> str:
    """Load the research strategy system prompt from external file."""
    if not RESEARCH_STRATEGY_PROMPT_FILE.exists():
        return "You are a research strategist. Suggest domain ideas and search queries."
    return RESEARCH_STRATEGY_PROMPT_FILE.read_text().strip()


def strip_thinking_block(content: str) -> str:
    """Remove leading <think> blocks the model may include."""
    if not content:
        return ""
    return THINKING_BLOCK_RE.sub("", content, count=1).strip()


def is_disallowed_domain(domain: str) -> bool:
    domain = domain.lower()
    if domain.startswith("www."):
        domain = domain[4:]
    if any(domain == base or domain.endswith(f".{base}") for base in DISALLOWED_BASE_DOMAINS):
        return True
    if any(domain == blocked or domain.endswith(f".{blocked}") for blocked in DISALLOWED_LINK_DOMAINS):
        return True
    if domain.endswith(".stackexchange.com"):
        return True
    return False


def normalize_search_query(query: str) -> Optional[str]:
    if not query:
        return None
    clean = query.strip().strip('"').strip("'")
    clean = re.sub(r'\s+', ' ', clean)
    if len(clean) < 5:
        return None
    # Require at least 3 alphabetic characters
    if sum(1 for c in clean if c.isalpha()) < 3:
        return None
    return clean


def sanitize_llm_list(items: List[str]) -> List[str]:
    cleaned = []
    seen = set()
    for raw in items:
        if not raw:
            continue
        text = raw.strip().strip('-').strip()
        if len(text) < 6:
            continue
        if re.fullmatch(r'[\.0-9]+', text):
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(text)
    return cleaned


def looks_like_boilerplate(candidate: "LinkCandidate") -> bool:
    """Detect obvious low-value pages like contact/privacy/careers."""
    url = candidate.url.lower()
    title = (candidate.title or "").lower()
    description = (candidate.description or "").lower()
    content = (candidate.content or "")[:400].lower()
    boiler_keywords = [
        "contact", "privacy", "terms", "legal", "copyright", "careers",
        "login", "signup", "subscribe", "newsletter", "cookies", "policy",
        "advertise", "sponsorship", "about us", "our team", "press media"
    ]
    if any(k in url for k in boiler_keywords):
        return True
    combined = " ".join([title, description, content])
    hits = sum(1 for k in boiler_keywords if k in combined)
    if hits >= 2:
        return True
    # Extremely short or generic content
    if len(candidate.content) < 600 and hits >= 1:
        return True
    return False


def looks_like_bad_page_type(candidate: "LinkCandidate") -> Optional[str]:
    """Reject homepages, search pages, generic hubs, and corporate/product fluff."""
    url = candidate.url.lower()
    title = (candidate.title or "").lower()
    description = (candidate.description or "").lower()
    domain = urlparse(candidate.url).netloc.lower()
    combined = " ".join([title, description, candidate.content[:500].lower()])

    artifact_exceptions = ["/object/", "/objects/", "/item/", "/items/", "/record/", "/records/"]
    bad_path_markers = [
        "/search", "?q=", "?query=", "/tag/", "/tags/", "/category/", "/categories/",
        "/topics/", "/collections", "/collection/", "/browse", "/directory", "/directories",
        "/index", "/about", "/about-us", "/team", "/careers", "/shop", "/products",
        "/pricing", "/services", "/solutions", "/platform", "/docs", "/documentation",
        "/forum/", "/forums/", "/thread/", "/threads/", "/events/", "/calendar/",
        "/compliance", "/accessibility",
    ]
    if any(marker in url for marker in bad_path_markers) and not any(marker in url for marker in artifact_exceptions):
        return "search/category/homepage/product page"

    if re.fullmatch(r"https?://[^/]+/?", candidate.url.strip()):
        return "homepage"

    generic_title_markers = [
        "search results", "home", "homepage", "about us", "products", "documentation",
        "pricing", "services", "resources", "blog", "news", "category", "archive",
        "all posts", "tag archive", "welcome to", "index of",
    ]
    if any(marker in title for marker in generic_title_markers):
        return "generic hub title"

    if any(marker in combined for marker in [
        "sign up", "book a demo", "request a demo", "schedule a call", "our platform",
        "contact sales", "free trial", "cookie policy", "privacy policy", "all rights reserved",
        "community forum", "threadmarks", "upcoming event", "event calendar", "accessibility statement",
    ]):
        return "corporate/marketing page"

    if domain.startswith("forum.") or domain.startswith("forums."):
        return "forum/community page"

    if domain.endswith(".gov") and len(candidate.content) < 900:
        return "thin institutional page"

    if len(candidate.content) < 350 and not candidate.description:
        return "too little substance"

    return None


def extract_theme_terms(theme: dict) -> List[str]:
    theme_name = theme.get("name", "")
    links_direction = theme.get("links", theme_name)
    raw_terms = re.findall(r"[A-Za-z][A-Za-z'-]{3,}", f"{theme_name} {links_direction}".lower())
    seen = set()
    clean = []
    for term in raw_terms:
        if term in {"about", "through", "their", "where", "which", "research", "stories", "history"}:
            continue
        if term not in seen:
            seen.add(term)
            clean.append(term)
    return clean


def _merge_unique_strings(*groups: List[str]) -> List[str]:
    merged: List[str] = []
    seen = set()
    for group in groups:
        for item in group or []:
            text = (item or "").strip()
            if not text:
                continue
            key = text.lower()
            if key in seen:
                continue
            seen.add(key)
            merged.append(text)
    return merged


def get_source_lane_catalog() -> dict:
    """Return the global source-lane catalog, overlaying YAML config on sensible defaults."""
    config = load_source_lanes()
    global_cfg = config.get("global", {})
    configured_lanes = global_cfg.get("lanes", {}) or {}
    lane_catalog = {}

    for lane_name in DEFAULT_SOURCE_LANE_ORDER:
        defaults = DEFAULT_SOURCE_LANES.get(lane_name, {})
        configured = configured_lanes.get(lane_name, {})
        lane_catalog[lane_name] = {
            "seed_urls": _merge_unique_strings(defaults.get("seed_urls", []), configured.get("seed_urls", [])),
            "seed_domains": _merge_unique_strings(defaults.get("seed_domains", []), configured.get("seed_domains", [])),
            "query_templates": _merge_unique_strings(defaults.get("query_templates", []), configured.get("query_templates", [])),
        }

    for lane_name, configured in configured_lanes.items():
        if lane_name not in lane_catalog:
            lane_catalog[lane_name] = {
                "seed_urls": _merge_unique_strings(configured.get("seed_urls", [])),
                "seed_domains": _merge_unique_strings(configured.get("seed_domains", [])),
                "query_templates": _merge_unique_strings(configured.get("query_templates", [])),
            }

    lane_order = _merge_unique_strings(
        global_cfg.get("lane_order", []),
        DEFAULT_SOURCE_LANE_ORDER,
        list(lane_catalog.keys()),
    )

    return {
        "lane_order": lane_order,
        "lanes": lane_catalog,
        "seed_urls": _merge_unique_strings(global_cfg.get("seed_urls", [])),
        "seed_domains": _merge_unique_strings(global_cfg.get("seed_domains", [])),
        "seed_queries": _merge_unique_strings(global_cfg.get("seed_queries", [])),
    }


def get_theme_discovery_plan(theme: dict) -> dict:
    """Build a lane-first discovery plan for the current theme."""
    config = load_source_lanes()
    catalog = get_source_lane_catalog()
    theme_name = theme.get("name", "")
    theme_plan = (config.get("themes") or {}).get(theme_name, {})
    lane_overrides = theme_plan.get("lane_overrides", {}) or {}

    preferred_lanes = _merge_unique_strings(
        theme_plan.get("preferred_lanes", []),
        catalog["lane_order"],
    )

    lane_plans = {}
    for lane_name in preferred_lanes:
        base = catalog["lanes"].get(lane_name, {})
        override = lane_overrides.get(lane_name, {})
        lane_plans[lane_name] = {
            "seed_urls": _merge_unique_strings(override.get("seed_urls", []), base.get("seed_urls", [])),
            "seed_domains": _merge_unique_strings(override.get("seed_domains", []), base.get("seed_domains", [])),
            "query_templates": _merge_unique_strings(override.get("query_templates", []), base.get("query_templates", [])),
        }
    return {
        "preferred_lanes": preferred_lanes,
        "seed_urls": _merge_unique_strings(catalog.get("seed_urls", []), theme_plan.get("seed_urls", [])),
        "seed_domains": _merge_unique_strings(catalog.get("seed_domains", []), theme_plan.get("seed_domains", [])),
        "seed_queries": _merge_unique_strings(catalog.get("seed_queries", []), theme_plan.get("seed_queries", [])),
        "lane_plans": lane_plans,
    }


def get_theme_lane_config(theme: dict) -> dict:
    config = load_source_lanes()
    return (config.get("themes") or {}).get(theme.get("name", ""), {}) or {}


def get_theme_trusted_domains(theme: dict) -> List[str]:
    plan = get_theme_discovery_plan(theme)
    domains: List[str] = []
    seen = set()

    def add_domain(raw_domain: str) -> None:
        normalized = (raw_domain or "").lower().strip().rstrip(".")
        if normalized.startswith("www."):
            normalized = normalized[4:]
        if not normalized or normalized in seen:
            return
        seen.add(normalized)
        domains.append(normalized)

    for domain in plan.get("seed_domains", []):
        add_domain(domain)
    for url in plan.get("seed_urls", []):
        try:
            add_domain(urlparse(url).netloc)
        except Exception:
            continue
    for lane_plan in plan.get("lane_plans", {}).values():
        for domain in lane_plan.get("seed_domains", []):
            add_domain(domain)
        for url in lane_plan.get("seed_urls", []):
            try:
                add_domain(urlparse(url).netloc)
            except Exception:
                continue
    return domains


def is_trusted_theme_domain(url: str, theme: dict) -> bool:
    try:
        domain = urlparse(url).netloc.lower().rstrip(".")
    except Exception:
        return False
    if domain.startswith("www."):
        domain = domain[4:]
    trusted_domains = get_theme_trusted_domains(theme)
    return any(domain == trusted or domain.endswith(f".{trusted}") for trusted in trusted_domains)


def _normalize_theme_terms(values: List[str], min_length: int = 4) -> List[str]:
    cleaned: List[str] = []
    seen = set()
    for value in values:
        normalized = re.sub(r"\s+", " ", (value or "").lower()).strip()
        if len(normalized) < min_length:
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        cleaned.append(normalized)
    return cleaned


def get_theme_focus_terms(theme: dict) -> List[str]:
    """Return theme-specific anchor phrases used to reject drift and improve relevance scoring."""
    lane_config = get_theme_lane_config(theme)
    return _normalize_theme_terms(_merge_unique_strings(
        lane_config.get("focus_terms", []),
        [theme.get("name", "")],
        [part.strip() for part in theme.get("links", "").split(",") if part.strip()],
    ))


def get_theme_drift_terms(theme: dict) -> List[str]:
    lane_config = get_theme_lane_config(theme)
    return _normalize_theme_terms(lane_config.get("drift_terms", []), min_length=3)


def get_theme_blocked_domains(theme: dict) -> List[str]:
    lane_config = get_theme_lane_config(theme)
    domains: List[str] = []
    seen = set()
    for domain in lane_config.get("blocked_domains", []) or []:
        normalized = (domain or "").lower().strip().rstrip(".")
        if normalized.startswith("www."):
            normalized = normalized[4:]
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        domains.append(normalized)
    return domains


def theme_focus_hits(candidate: "LinkCandidate", theme: dict) -> List[str]:
    text = " ".join([
        candidate.url,
        candidate.title,
        candidate.description,
        " ".join(candidate.concepts[:8]),
        candidate.content[:1800],
    ]).lower()
    return [term for term in get_theme_focus_terms(theme) if term and term in text]


def theme_drift_hits(candidate: "LinkCandidate", theme: dict) -> List[str]:
    text = " ".join([
        candidate.url,
        candidate.title,
        candidate.description,
        " ".join(candidate.concepts[:8]),
        candidate.content[:1500],
    ]).lower()
    return [term for term in get_theme_drift_terms(theme) if term and term in text]


def is_theme_blocked_domain(url: str, theme: dict) -> bool:
    try:
        domain = urlparse(url).netloc.lower().rstrip(".")
    except Exception:
        return False
    if domain.startswith("www."):
        domain = domain[4:]
    blocked_domains = get_theme_blocked_domains(theme)
    return any(domain == blocked or domain.endswith(f".{blocked}") for blocked in blocked_domains)


def get_theme_rejection_reason(candidate: "LinkCandidate", theme: dict) -> Optional[str]:
    if is_theme_blocked_domain(candidate.url, theme):
        return "theme-blocked domain"

    if theme_focus_hits(candidate, theme):
        return None

    drift_hits = theme_drift_hits(candidate, theme)
    if not drift_hits:
        return None

    title_text = " ".join([candidate.url, candidate.title, candidate.description]).lower()
    if any(term in title_text for term in drift_hits) or len(drift_hits) >= 2:
        return f"theme drift: {drift_hits[0]}"
    return None


def looks_off_theme(candidate: "LinkCandidate", theme: dict) -> bool:
    """Reject pages that never meaningfully touch the theme, even if they are obscure."""
    hits = theme_focus_hits(candidate, theme)
    if hits:
        return False

    generic_terms = [term for term in extract_theme_terms(theme) if len(term) >= 5]
    if not generic_terms:
        return False

    title_text = " ".join([
        candidate.url,
        candidate.title,
        candidate.description,
    ]).lower()
    body_text = " ".join([
        " ".join(candidate.concepts[:8]),
        candidate.content[:1000],
    ]).lower()
    title_hits = sum(1 for term in generic_terms if term in title_text)
    body_hits = sum(1 for term in generic_terms if term in body_text)
    if title_hits >= 1 and body_hits >= 1:
        return False

    text = " ".join([
        candidate.url,
        candidate.title,
        candidate.description,
        " ".join(candidate.concepts[:8]),
        candidate.content[:1000],
    ]).lower()
    generic_hits = sum(1 for term in generic_terms if term in text)
    return generic_hits < 2


def build_lane_query(template: str, theme: dict, lane_name: str) -> str:
    """Render a lane query template with theme-specific terms."""
    theme_name = theme.get("name", "")
    links_direction = theme.get("links", theme_name)
    direction_focus = links_direction.split(",")[0].strip() if "," in links_direction else links_direction
    terms = extract_theme_terms(theme)
    primary_term = terms[0] if terms else theme_name
    secondary_term = terms[1] if len(terms) > 1 else primary_term
    theme_terms = " ".join(terms[:4]) or theme_name
    lane_label = lane_name.replace("-", " ")
    try:
        return template.format(
            theme_name=theme_name,
            links_direction=direction_focus,
            primary_term=primary_term,
            secondary_term=secondary_term,
            theme_terms=theme_terms,
            lane_name=lane_label,
        )
    except KeyError:
        return template


def looks_like_bad_url_shape(url: str, anchor_text: str = "") -> bool:
    text = f"{url.lower()} {(anchor_text or '').lower()}"
    artifact_exceptions = ["/object/", "/objects/", "/item/", "/items/", "/record/", "/records/"]
    bad_markers = [
        "/search", "?q=", "?query=", "/tag/", "/tags/", "/category/", "/categories/",
        "/topics/", "/collections", "/collection/", "/browse", "/directory", "/directories/",
        "/about", "/contact", "/privacy", "/terms", "/shop", "/products", "/pricing",
        "/services", "/solutions", "/platform", "/docs", "/documentation", "/feed",
        "/rss", ".rss", ".xml", "sitemap", "/wp-json", "/author/", "/page/", "login", "signup", "subscribe",
        "/forum/", "/forums/", "/thread/", "/threads/", "threadmarks", "/events/", "/calendar/",
        "compliance", "accessibility",
    ]
    return any(marker in text for marker in bad_markers) and not any(marker in text for marker in artifact_exceptions)


def score_seed_link(candidate_url: str, anchor_text: str, seed_domain: str) -> float:
    score = 0.0
    parsed = urlparse(candidate_url)
    path = parsed.path or "/"
    depth = len([part for part in path.split("/") if part])

    if parsed.netloc.lower().rstrip(".").startswith("www."):
        domain = parsed.netloc.lower().rstrip(".")[4:]
    else:
        domain = parsed.netloc.lower().rstrip(".")

    if domain == seed_domain:
        score += 0.25

    score += min(depth * 0.08, 0.32)

    if re.search(r"/(article|articles|essay|essays|object|objects|station|manual|history|record|entry|report|notes|archive)/", path.lower()):
        score += 0.2
    if re.search(r"\d", path):
        score += 0.08

    anchor_len = len((anchor_text or "").strip())
    if 10 <= anchor_len <= 90:
        score += 0.1

    if looks_like_bad_url_shape(candidate_url, anchor_text):
        score -= 0.5

    return score


def crawl_seed_page(seed_url: str, max_links: int = 10) -> List[str]:
    """Extract promising child pages from a curated seed page."""
    if _network_disabled_for_run:
        print(f"    ⚠️  Skipping seed crawl; fresh web discovery disabled for run")
        return []
    try:
        response = request_with_retries(
            build_requests_session(headers={"User-Agent": random.choice(DDG_USER_AGENTS)}),
            "get",
            seed_url,
            retry_label="seed crawl",
            timeout=SEARCH_TIMEOUT,
        )
        if response.status_code != 200 or "text/html" not in response.headers.get("Content-Type", "").lower():
            return []
    except Exception as e:
        print(f"    ⚠️  Seed crawl failed for {seed_url[:60]}...: {e}")
        return []

    soup = BeautifulSoup(response.text, "html.parser")
    seed_domain = urlparse(seed_url).netloc.lower().rstrip(".")
    if seed_domain.startswith("www."):
        seed_domain = seed_domain[4:]

    scored_links = []
    seen = set()
    for link in soup.find_all("a", href=True):
        href = link.get("href", "").strip()
        text = link.get_text(" ", strip=True)
        if not href:
            continue
        absolute = requests.compat.urljoin(seed_url, href)
        if not absolute.startswith("http"):
            continue
        normalized = normalize_url(absolute)
        if normalized in seen:
            continue
        seen.add(normalized)

        try:
            parsed = urlparse(absolute)
        except Exception:
            continue

        domain = parsed.netloc.lower().rstrip(".")
        if domain.startswith("www."):
            domain = domain[4:]

        if domain != seed_domain:
            continue
        if is_disallowed_domain(domain):
            continue
        if looks_like_bad_url_shape(absolute, text):
            continue

        score = score_seed_link(absolute, text, seed_domain)
        if score <= 0:
            continue
        scored_links.append((score, absolute))

    scored_links.sort(key=lambda item: item[0], reverse=True)
    return [url for _, url in scored_links[:max_links]]


def dedupe_candidate_urls(urls: List[str], limit: Optional[int] = None) -> List[str]:
    clean: List[str] = []
    seen = set()
    for url in urls:
        try:
            domain = urlparse(url).netloc.lower()
        except Exception:
            continue
        if not domain or is_disallowed_domain(domain) or looks_like_bad_url_shape(url):
            continue
        normalized = normalize_url(url)
        if normalized in seen:
            continue
        seen.add(normalized)
        clean.append(url)
        if limit and len(clean) >= limit:
            break
    return clean


def search_seed_domain(domain: str, queries: List[str], max_results: int = 8) -> List[str]:
    """Search within a trusted domain using lane-specific queries."""
    collected: List[str] = []
    for query in queries:
        site_query = f"site:{domain} {query}".strip()
        results = search_marginalia(site_query, max_results=max_results)
        if not results:
            results = search_duckduckgo(site_query, max_results=min(4, max_results))
        collected.extend(results)
        if len(dedupe_candidate_urls(collected)) >= max_results:
            break
    return dedupe_candidate_urls(collected, limit=max_results)


def expand_candidate_neighborhood(seed_urls: List[str], max_seed_pages: int = 4, max_links: int = 4) -> List[str]:
    """Crawl a small neighborhood around promising pages to find adjacent hidden gems."""
    expanded: List[str] = []
    for seed_url in dedupe_candidate_urls(seed_urls, limit=max_seed_pages):
        expanded.extend(crawl_seed_page(seed_url, max_links=max_links))
    return dedupe_candidate_urls(expanded, limit=max_seed_pages * max_links)


def get_curated_seed_urls(theme: dict) -> List[str]:
    """Return URLs from globally trusted seed pages and theme-specific scout queries."""
    plan = get_theme_discovery_plan(theme)
    discovered: List[str] = []

    if plan.get("seed_urls"):
        print("\n  Curated seed pages...")
        for seed_url in plan["seed_urls"][:8]:
            print(f"    Seed: {seed_url}")
            discovered.append(seed_url)
            discovered.extend(crawl_seed_page(seed_url, max_links=6))

    if plan.get("seed_domains"):
        print("\n  Curated scout domains...")
        for domain in plan["seed_domains"][:6]:
            scout_query = build_lane_query('"{theme_name}" "{links_direction}"', theme, "theme-scouting")
            discovered.extend(search_seed_domain(domain, [scout_query], max_results=4))

    if plan.get("seed_queries"):
        print("\n  Curated scout queries...")
        for query in plan["seed_queries"][:6]:
            rendered = build_lane_query(query, theme, "theme-scouting")
            discovered.extend(search_marginalia(rendered, max_results=5))

    clean = dedupe_candidate_urls(discovered, limit=24)
    print(f"  Curated scout seeds produced {len(clean)} candidate URLs")
    return clean


def discover_lane_urls(theme: dict, lane_name: str, lane_plan: dict) -> List[str]:
    """Discover promising candidates by exploring one specific source lane."""
    discovered: List[str] = []
    rendered_queries = [build_lane_query(query, theme, lane_name) for query in lane_plan.get("query_templates", [])[:4]]

    print(f"\n  Lane: {lane_name}")

    for seed_url in lane_plan.get("seed_urls", [])[:6]:
        print(f"    Seed page: {seed_url}")
        discovered.append(seed_url)
        discovered.extend(crawl_seed_page(seed_url, max_links=5))

    for domain in lane_plan.get("seed_domains", [])[:5]:
        print(f"    Trusted domain: {domain}")
        discovered.extend(search_seed_domain(domain, rendered_queries, max_results=5))

    for query in rendered_queries:
        print(f"    Lane query: {query[:100]}")
        discovered.extend(search_marginalia(query, max_results=4))

    neighborhood = expand_candidate_neighborhood(discovered, max_seed_pages=4, max_links=4)
    if neighborhood:
        print(f"    Expanded neighborhood: {len(neighborhood)} adjacent pages")
        discovered.extend(neighborhood)

    clean = dedupe_candidate_urls(discovered, limit=16)
    print(f"    Lane yielded {len(clean)} candidate URLs")
    return clean


def ensure_minimum_entries(primary: List[str], backups: List[str], minimum: int) -> List[str]:
    merged = []
    seen = set()
    for item in primary + backups:
        if not item:
            continue
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
        if len(merged) >= minimum:
            break
    return merged


def search_marginalia(query: str, max_results: int = 8) -> List[str]:
    """Fallback search using Marginalia (indie search engine)."""
    clean_query = query.strip()
    if not clean_query:
        return []
    if _network_disabled_for_run:
        print("      ⚠️  Skipping Marginalia; fresh web discovery disabled for run")
        return []
    headers = {
        'User-Agent': random.choice(DDG_USER_AGENTS),
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'
    }
    try:
        response = request_with_retries(
            build_requests_session(headers=headers),
            "get",
            "https://search.marginalia.nu/search",
            retry_variants=False,
            retry_label="Marginalia",
            params={"query": clean_query},
            timeout=SEARCH_TIMEOUT,
        )
        if response.status_code != 200:
            print(f"      ⚠️  Marginalia status {response.status_code}")
            return []
        soup = BeautifulSoup(response.text, 'html.parser')
        urls = []
        for link in soup.find_all('a', href=True):
            href = link.get('href', '')
            if href.startswith('http') and 'marginalia.nu' not in href:
                urls.append(href)
        seen = set()
        clean = []
        for url in urls:
            if url not in seen:
                seen.add(url)
                clean.append(url)
        if clean:
            print(f"      ✅ Marginalia fallback returned {len(clean)} URLs")
        return clean[:max_results]
    except Exception as e:
        print(f"      ⚠️  Marginalia fallback failed: {e}")
        return []


def generate_backup_domain_ideas(theme_name: str, links_direction: str) -> List[str]:
    base = theme_name.lower()
    direction = links_direction.lower()
    return [
        f"Personal research pages documenting one odd corner of {base} in relation to {direction}.",
        f"Community archives or local history sites with one unusually specific {base} artifact or story.",
        f"Hobbyist restoration logs, field notes, or object writeups tied to {base} and {direction}.",
        f"Dead-web or old-web pages preserving a failed project, strange document, or forgotten system about {base}.",
        f"Museum object pages or oral-history fragments where {base} appears in a singular, concrete way.",
    ]


def generate_backup_search_queries(theme_name: str, links_direction: str) -> List[str]:
    combos = [
        f'"{theme_name}" "field notes" "{links_direction}" -wikipedia -github -site:medium.com',
        f'"{theme_name}" "oral history" "{links_direction}" -site:.edu -site:archive.org',
        f'"{theme_name}" "museum object" "{links_direction}" -shop -store',
        f'"{theme_name}" "personal research" -wikipedia -site:substack.com',
        f'"{theme_name}" "forgotten project" "{links_direction}"',
    ]
    return combos


def filter_llm_urls(urls: List[str]) -> List[str]:
    filtered = []
    seen = set()
    for url in urls:
        try:
            parsed = urlparse(url)
        except Exception:
            continue
        domain = parsed.netloc.lower()
        if not domain or is_disallowed_domain(domain):
            continue
        if url in seen:
            continue
        seen.add(url)
        filtered.append(url)
    return filtered[:5]


def filter_llm_direct_urls(urls: List[str], theme: dict) -> List[str]:
    """Only trust direct URLs from the model if they stay inside curated source neighborhoods."""
    plan = get_theme_discovery_plan(theme)
    allowed_domains = set()
    for domain in plan.get("seed_domains", []):
        allowed_domains.add(domain.lower().lstrip("www."))
    for url in plan.get("seed_urls", []):
        parsed = urlparse(url)
        domain = parsed.netloc.lower().rstrip(".")
        if domain.startswith("www."):
            domain = domain[4:]
        if domain:
            allowed_domains.add(domain)
    for lane_plan in plan.get("lane_plans", {}).values():
        for domain in lane_plan.get("seed_domains", []):
            allowed_domains.add(domain.lower().lstrip("www."))
        for url in lane_plan.get("seed_urls", []):
            parsed = urlparse(url)
            domain = parsed.netloc.lower().rstrip(".")
            if domain.startswith("www."):
                domain = domain[4:]
            if domain:
                allowed_domains.add(domain)

    filtered = []
    for url in filter_llm_urls(urls):
        try:
            domain = urlparse(url).netloc.lower().rstrip(".")
        except Exception:
            continue
        if domain.startswith("www."):
            domain = domain[4:]
        if domain in allowed_domains:
            filtered.append(url)
    return filtered[:5]


def load_themes() -> dict:
    """Load unified themes configuration from YAML file."""
    if not THEMES_FILE.exists():
        print(f"Error: Themes file not found at {THEMES_FILE}")
        sys.exit(1)
    return yaml.safe_load(THEMES_FILE.read_text()) or {}


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


class LinkCandidate:
    """Represents a potential link with its metadata and scores."""
    def __init__(self, url: str, title: str = "", description: str = ""):
        self.url = url
        self.title = title
        self.description = description
        self.content = ""
        self.concepts: List[str] = []
        self.interesting_bits: List[str] = []
        self.relevance_score = 0.0
        self.obscurity_score = 0.0
        self.gem_score = 0.0
        self.story_seed_score = 0.0
        self.anti_corporate_score = 0.0
        self.final_score = 0.0
        self.error: Optional[str] = None
        self.curator_reason: str = ""
        
    def to_dict(self) -> Dict:
        return {
            "url": self.url,
            "title": self.title,
            "description": self.description,
            "relevance_score": self.relevance_score,
            "obscurity_score": self.obscurity_score,
            "gem_score": self.gem_score,
            "story_seed_score": self.story_seed_score,
            "anti_corporate_score": self.anti_corporate_score,
            "final_score": self.final_score,
            "concepts": self.concepts[:5],
            "interesting_bits": self.interesting_bits[:5],
            "curator_reason": self.curator_reason,
        }


def search_duckduckgo(query: str, max_results: int = 10) -> List[str]:
    """Search DuckDuckGo Lite with throttling/retries and parse direct/redirect URLs."""
    if not query:
        return []
    if _network_disabled_for_run:
        print("      ⚠️  Skipping DuckDuckGo; fresh web discovery disabled for run")
        return []

    clean_query = query.strip().strip('"').strip("'")

    global _ddg_disabled_for_run, _ddg_queries_this_run
    if _ddg_disabled_for_run:
        print("      ⚠️  DuckDuckGo disabled for this run; skipping query")
        return search_marginalia(clean_query, max_results=max_results)

    if _ddg_queries_this_run >= DDG_MAX_QUERIES_PER_RUN:
        print(f"      ⚠️  DDG budget exhausted ({DDG_MAX_QUERIES_PER_RUN} query/run); using Marginalia")
        return search_marginalia(clean_query, max_results=max_results)

    _ddg_queries_this_run += 1

    for attempt in range(DDG_MAX_RETRIES):
        headers = {
            'User-Agent': random.choice(DDG_USER_AGENTS),
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.8',
            'Referer': 'https://duckduckgo.com/',
        }
        delay = DDG_MIN_INTERVAL * (DDG_THROTTLE_FACTOR ** attempt)
        _throttle_ddg(delay)
        try:
            session = build_requests_session(headers=headers)
            endpoints = [
                ("post", "https://lite.duckduckgo.com/lite/", {'q': clean_query, 'kl': 'us-en'}),
                ("get", f"https://lite.duckduckgo.com/lite/?q={quote_plus(clean_query)}&kl=us-en", None),
                ("get", f"https://duckduckgo.com/html/?q={quote_plus(clean_query)}&ia=web", None),
            ]

            response = None
            for method, url, data in endpoints:
                print(f"      🔍 DDG: {clean_query[:60]}... ({method.upper()} {url.split('//')[1][:30]}...) (attempt {attempt + 1})")
                if method == "post":
                    response = request_with_retries(
                        session,
                        "post",
                        url,
                        attempts=2,
                        retry_variants=False,
                        retry_label="DDG",
                        data=data,
                        timeout=SEARCH_TIMEOUT,
                        allow_redirects=True,
                    )
                else:
                    response = request_with_retries(
                        session,
                        "get",
                        url,
                        attempts=2,
                        retry_variants=False,
                        retry_label="DDG",
                        timeout=SEARCH_TIMEOUT,
                        allow_redirects=True,
                    )
                print(f"      📄 Status: {response.status_code}")
                if response.status_code in (200, 202):
                    break
                if response.status_code == 429:
                    print("      ⚠️  DDG rate-limited (429). Backing off...")
                    time.sleep(2)
                elif response.status_code == 403:
                    print("      ⚠️  DDG returned 403 (blocked). Trying alternate endpoint...")
                else:
                    print(f"      ⚠️  DDG unexpected status {response.status_code}")
                response = None

            if not response:
                continue

            if response.status_code == 202:
                print("      ⚠️  DDG returned 202 (throttled). Waiting and retrying...")
                time.sleep(1)
                continue

            soup = BeautifulSoup(response.text, 'html.parser')
            raw_urls = []
            for link in soup.find_all('a', href=True):
                href = link.get('href', '')
                candidate = None
                if href.startswith('http') and 'duckduckgo.com' not in href:
                    candidate = href
                elif 'uddg=' in href:
                    parsed = urlparse(href)
                    params = parse_qs(parsed.query)
                    redirect = params.get('uddg', [None])[0]
                    if redirect:
                        candidate = unquote(redirect)

                if candidate and candidate.startswith('http'):
                    raw_urls.append(candidate)

            print(f"      🔗 Found {len(raw_urls)} URLs")
            for i, url in enumerate(raw_urls[:3], 1):
                print(f"         {i}. {url[:60]}...")

            skip_domains = {'duckduckgo.com', 'google.com', 'bing.com', 'facebook.com',
                            'twitter.com', 'instagram.com', 'youtube.com'}
            seen = set()
            clean_urls = []
            for url in raw_urls:
                domain = urlparse(url).netloc.lower()
                if any(skip in domain for skip in skip_domains):
                    continue
                if url not in seen:
                    seen.add(url)
                    clean_urls.append(url)

            print(f"      ✅ Cleaned to {len(clean_urls)} unique")
            if clean_urls:
                _record_ddg_success()
                return clean_urls[:max_results]

        except Exception as e:
            print(f"      ❌ DDG failed (attempt {attempt + 1}): {e}")

    _record_ddg_failure()
    print("      ❌ DDG exhausted retries with no results")
    fallback = search_marginalia(clean_query, max_results=max_results)
    return fallback


def search_academic_sources(theme: str, links_direction: str) -> List[str]:
    """Search academic and archival sources for relevant content."""
    if _network_disabled_for_run:
        print("  ⚠️  Skipping academic search; fresh web discovery disabled for run")
        return []
    urls = []
    
    # Build search terms
    terms = f"{theme} {links_direction}"
    query = quote_plus(terms)
    
    # arXiv search
    try:
        arxiv_url = f"http://export.arxiv.org/api/query?search_query=all:{query}&start=0&max_results=5"
        response = request_with_retries(
            build_requests_session(),
            "get",
            arxiv_url,
            retry_variants=False,
            retry_label="arXiv",
            timeout=SEARCH_TIMEOUT,
        )
        if response.status_code == 200:
            # Parse arXiv IDs from XML
            ids = re.findall(r'<id>(http://arxiv.org/abs/\d+\.\d+)</id>', response.text)
            urls.extend(ids)
            print(f"  Found {len(ids)} results from arXiv")
    except Exception as e:
        print(f"  arXiv search failed: {e}")
    
    # Filter out disallowed domains before returning
    return [u for u in urls if not is_disallowed_domain(urlparse(u).netloc)]


def _extract_section(content: str, header: str, headers: List[str]) -> str:
    if header not in content:
        return ""
    after = content.split(f"{header}:", 1)[1]
    stops = []
    for other in headers:
        if other == header:
            continue
        marker = f"{other}:"
        idx = after.find(marker)
        if idx >= 0:
            stops.append(idx)
    if stops:
        after = after[:min(stops)]
    return after.strip()


def _parse_bulleted_text(text: str) -> List[str]:
    entries = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        line = re.sub(r'^[\-\*\d\.\)]+\s*', '', line).strip()
        if line:
            entries.append(line)
    return entries


def get_llm_research_strategy(theme: dict) -> Tuple[List[str], List[str], List[str]]:
    """Ask LLM for domain ideas, search queries, and direct URLs."""
    if not API_KEY:
        print("    ⚠️  No API key available")
        return [], [], []
    if OpenAI is None:
        print(f"    ⚠️  OpenAI client unavailable: {OPENAI_IMPORT_ERROR}")
        return [], [], []

    theme_name = theme.get("name", "")
    links_direction = theme.get("links", theme_name)

    system_prompt = (
        load_system_prompt().strip()
        + "\n\n"
        + load_research_strategy_prompt().format(
        theme_name=theme_name,
        links_direction=links_direction
        )
    )

    headers = ["DOMAIN IDEAS", "SEARCH QUERIES", "URLs FOUND"]

    best_domain: List[str] = []
    best_queries: List[str] = []
    best_urls: List[str] = []

    for attempt in range(3):
        try:
            temperature = 0.45 + (attempt * 0.15)
            print(f"    🤖 LLM attempt {attempt + 1} (temp={temperature:.2f})")
            client = OpenAI(
                api_key=API_KEY,
                base_url=API_BASE,
                max_retries=OPENAI_MAX_RETRIES,
                timeout=OPENAI_REQUEST_TIMEOUT,
            )
            response = client.chat.completions.create(
                model=MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"Research topic: {theme_name}"}
                ],
                temperature=temperature,
                max_tokens=1000,
                timeout=OPENAI_REQUEST_TIMEOUT,
            )

            content = strip_thinking_block(response.choices[0].message.content or "")
            print(f"    📄 LLM output (first 300 chars): {content[:300]}...")

            raw_domain = _parse_bulleted_text(_extract_section(content, "DOMAIN IDEAS", headers))
            raw_queries = _parse_bulleted_text(_extract_section(content, "SEARCH QUERIES", headers))
            raw_urls = re.findall(r"https?://[^\s<>\"'\)\]\}]+", _extract_section(content, "URLs FOUND", headers) or content)

            best_domain = sanitize_llm_list(raw_domain)
            best_queries = [q for q in (normalize_search_query(q) for q in sanitize_llm_list(raw_queries)) if q]
            best_urls = filter_llm_direct_urls(raw_urls, theme)

            if len(best_domain) >= MIN_LLM_DOMAIN_IDEAS and len(best_queries) >= MIN_LLM_SEARCH_QUERIES:
                break

        except Exception as e:
            print(f"    LLM attempt {attempt + 1} failed: {e}")

    if len(best_domain) < MIN_LLM_DOMAIN_IDEAS:
        print("    ⚠️  Using backup domain ideas")
        best_domain = ensure_minimum_entries(best_domain, generate_backup_domain_ideas(theme_name, links_direction), MIN_LLM_DOMAIN_IDEAS)

    if len(best_queries) < MIN_LLM_SEARCH_QUERIES:
        print("    ⚠️  Using backup search queries")
        best_queries = ensure_minimum_entries(best_queries, generate_backup_search_queries(theme_name, links_direction), MIN_LLM_SEARCH_QUERIES)

    print(f"    ✅ LLM suggested {len(best_domain)} domain ideas")
    for i, idea in enumerate(best_domain[:3], 1):
        print(f"       {i}. {idea}")
    print(f"    ✅ LLM suggested {len(best_queries)} search queries")
    for i, query in enumerate(best_queries[:3], 1):
        print(f"       {i}. {query}")
    print(f"    ✅ LLM suggested {len(best_urls)} trusted direct URLs")

    return best_domain, best_queries, best_urls


def get_candidate_urls(theme: dict, registry: Optional[LinkRegistry] = None) -> List[str]:
    """Generate candidate URLs using lane-first discovery across trusted web neighborhoods."""
    theme_name = theme.get("name", "")
    print(f"\nTheme: {theme_name}")

    plan = get_theme_discovery_plan(theme)
    all_urls: List[str] = []

    # 0. Scout from globally trusted seeds and explicit theme-level seeds.
    all_urls.extend(get_curated_seed_urls(theme))

    # 1. Explore trusted source lanes in order, keeping each lane bounded.
    for lane_name in plan["preferred_lanes"][:5]:
        if _network_disabled_for_run:
            print("\n  ⚠️  Network looks unavailable; stopping fresh lane discovery")
            break
        lane_urls = discover_lane_urls(theme, lane_name, plan["lane_plans"].get(lane_name, {}))
        all_urls.extend(lane_urls)

    # 2. Ask the LLM for a few extra search angles, but keep it inside lane-first search.
    if _network_disabled_for_run:
        print("\n  ⚠️  Skipping live search expansion; fresh web discovery disabled for run")
    else:
        print("\n  LLM search angles...")
        _, llm_queries, llm_direct_urls = get_llm_research_strategy(theme)
        all_urls.extend(filter_llm_direct_urls(llm_direct_urls, theme))
        for query in llm_queries[:3]:
            print(f"    Angle: {query[:100]}")
            all_urls.extend(search_marginalia(query, max_results=4))

    # 3. If the pool is still thin, widen within the same architecture using lane queries only.
    all_urls = dedupe_candidate_urls(all_urls)
    if len(all_urls) < 25 and not _network_disabled_for_run:
        print(f"\n  ⚠️  Only {len(all_urls)} candidates from lane discovery; widening within trusted lanes")
        for lane_name in plan["preferred_lanes"][:3]:
            lane_plan = plan["lane_plans"].get(lane_name, {})
            for query in lane_plan.get("query_templates", [])[:2]:
                rendered = build_lane_query(query, theme, lane_name)
                all_urls.extend(search_marginalia(rendered, max_results=5))
        for query in plan.get("seed_queries", [])[:3]:
            rendered = build_lane_query(query, theme, "theme-scouting")
            all_urls.extend(search_marginalia(rendered, max_results=5))

    all_urls = dedupe_candidate_urls(all_urls)

    # Filter previously-published URLs via registry
    if registry:
        all_urls, rejected = registry.filter_new(all_urls)
        if rejected:
            print(f"\n  🗂️  Registry rejected {rejected} previously-published URLs")

    unique_urls = dedupe_candidate_urls(all_urls, limit=MAX_CANDIDATES)

    print(f"\nTotal unique candidates: {len(all_urls)}")
    return unique_urls


def is_listicle_url(url: str, title: str = "") -> bool:
    """Detect if URL/title appears to be a listicle/junk article or collection."""
    # Domain-level block
    try:
        domain = urlparse(url).netloc.lower()
        if domain.startswith("www."):
            domain = domain[4:]
        if domain in DISALLOWED_LINK_DOMAINS:
            return True
    except Exception:
        pass

    listicle_patterns = [
        # Numbered listicles — broad: "N <word>" at start of title
        r'^\d+\s+\w+',
        r'\d+\s+(forgotten|abandoned|obsolete|lost|hidden|secret|amazing|incredible|surprising|weird)',
        r'top\s+\d+',
        r'\d+\s+best',
        r'\d+\s+worst',
        r'\d+\s+things?\s+(you|to|that)',
        r'\d+\s+(unsolved|bizarre|crazy|close\s+calls?|ways)',
        # List/collection content (even from academic sources)
        r'list\s+of',
        r'listicle',
        r'timeline\s+of',
        r'famous',
        r'notable',
        r'greatest',
        r'guide\s+to',
        r'research\s+guide',
        r'library\s+guide',
        r'collection\s+of',
        r'category:',
        r'index\s+of',
        # Clickbait sites (redundant with domain block but keeps URL-path checks)
        r'listicle-site',
        r'buzzfeed',
        r'boredpanda',
        r'viralnova',
        r'ranker',
        r'list25',
        r'\d+-facts?',
        r'mind-blowing',
        r'will-blow-your-mind',
        r'you-won.t-believe',
        r'won.t-believe',
        # Game guides / tips pages
        r'tips?\s+(and|&)\s+tricks?',
        r'game\s+tips',
        r'survival\s+tips',
    ]
    
    combined_text = f"{url} {title}".lower()
    
    for pattern in listicle_patterns:
        if re.search(pattern, combined_text, re.IGNORECASE):
            return True
    
    # Check title specifically for leading number pattern (e.g. "5 Cold War Close Calls")
    title_stripped = title.strip()
    if re.match(r'^\d{1,2}\s+', title_stripped):
        return True
    
    # Check for library guide URLs (.edu sites with /guides/, /research/, etc)
    library_guide_patterns = [
        r'/guides?/',
        r'research.*guide',
        r'libguides',
        r'library.*guide',
        r'subject.*guide',
    ]
    for pattern in library_guide_patterns:
        if re.search(pattern, url, re.IGNORECASE):
            return True
    
    return False


def scrape_and_analyze(urls: List[str], theme: dict) -> List[LinkCandidate]:
    """Scrape content from URLs and analyze relevance."""
    scraper = WebScraper()
    candidates = []
    listicle_count = 0
    failed_count = 0
    success_count = 0
    theme_reject_count = 0
    off_theme_count = 0
    
    print(f"\nScraping {len(urls)} candidates...")
    
    for i, url in enumerate(urls, 1):
        print(f"\n  [{i}/{len(urls)}] {url}")
        
        candidate = LinkCandidate(url)
        
        # Scrape the page
        scraped = scraper.scrape_url(url)
        
        if scraped.error:
            print(f"    ✗ Failed: {scraped.error}")
            candidate.error = scraped.error
            failed_count += 1
            continue
        
        # Store scraped data
        candidate.title = scraped.title
        candidate.description = scraped.description
        candidate.content = scraped.content[:3000]  # Limit content
        candidate.concepts = scraped.concepts
        candidate.interesting_bits = scraped.interesting_bits
        candidate.obscurity_score = scraped.obscurity_score
        
        # Reject listicles early
        if is_listicle_url(candidate.url, candidate.title):
            print(f"    ✗ Rejected (listicle/junk): {scraped.title[:60]}...")
            print(f"    🔍 Listicle patterns matched in: {url}")
            candidate.error = "Listicle/junk content detected"
            listicle_count += 1
            continue

        bad_page_reason = looks_like_bad_page_type(candidate)
        if bad_page_reason:
            print(f"    ✗ Rejected ({bad_page_reason}): {scraped.title[:60]}...")
            candidate.error = bad_page_reason
            continue
        
        # Allow trusted museum/institution .edu pages only when they come from the theme's curated lanes.
        if urlparse(candidate.url).netloc.endswith('.edu') and not is_trusted_theme_domain(candidate.url, theme):
            print(f"    ✗ Rejected (untrusted .edu domain): {scraped.title[:60]}...")
            candidate.error = ".edu domain filtered"
            continue

        theme_rejection = get_theme_rejection_reason(candidate, theme)
        if theme_rejection:
            print(f"    ✗ Rejected ({theme_rejection}): {scraped.title[:60]}...")
            candidate.error = theme_rejection
            theme_reject_count += 1
            continue

        if looks_off_theme(candidate, theme):
            print(f"    ✗ Rejected (off-theme drift): {scraped.title[:60]}...")
            candidate.error = "off-theme drift"
            off_theme_count += 1
            continue
        
        print(f"    ✓ Scraped: {scraped.title[:60]}...")
        print(f"    Concepts: {', '.join(scraped.concepts[:5])}")
        focus_hits = theme_focus_hits(candidate, theme)
        if focus_hits:
            print(f"    Focus hits: {', '.join(focus_hits[:4])}")
        print(f"    Obscurity: {scraped.obscurity_score:.2f}")
        success_count += 1
        
        candidates.append(candidate)
    
    print(f"\n📊 Scraping Summary:")
    print(f"  ✓ Successfully scraped: {success_count}")
    print(f"  ✗ Failed: {failed_count}")
    print(f"  ⏭ Filtered as listicles: {listicle_count}")
    print(f"  ⏭ Filtered by theme blocks: {theme_reject_count}")
    print(f"  ⏭ Filtered as off-theme drift: {off_theme_count}")
    
    return candidates


def calculate_relevance_score(candidate: LinkCandidate, theme: dict) -> float:
    """Calculate how relevant the content is to the theme."""
    theme_words = set(extract_theme_terms(theme))
    focus_terms = get_theme_focus_terms(theme)
    drift_terms = get_theme_drift_terms(theme)
    
    score = 0.0
    content_lower = candidate.content.lower()
    title_lower = candidate.title.lower()
    description_lower = candidate.description.lower()
    concept_text = " ".join(candidate.concepts[:8]).lower()
    combined = " ".join([candidate.url.lower(), title_lower, description_lower, concept_text, content_lower])

    # Theme-specific anchor phrases matter more than generic keyword overlap.
    title_focus_matches = sum(1 for term in focus_terms if term in title_lower)
    score += min(title_focus_matches * 0.30, 0.60)

    body_focus_matches = sum(1 for term in focus_terms if term in combined)
    score += min(body_focus_matches * 0.10, 0.30)

    drift_matches = sum(1 for term in drift_terms if term in combined)
    if drift_matches and body_focus_matches == 0:
        score -= min(drift_matches * 0.10, 0.30)
    
    # Check for theme word matches in title (high weight)
    title_matches = sum(1 for word in theme_words if word in title_lower)
    score += min(title_matches * 0.12, 0.24)
    
    # Check for theme word matches in content
    content_matches = sum(1 for word in theme_words if word in combined)
    score += min(content_matches * 0.03, 0.18)
    
    # Check concept relevance
    concept_matches = 0
    for concept in candidate.concepts:
        concept_lower = concept.lower()
        if any(word in concept_lower for word in theme_words):
            concept_matches += 1
        if any(term in concept_lower for term in focus_terms):
            concept_matches += 1
    score += min(concept_matches * 0.08, 0.24)
    
    return min(max(score, 0.0), 1.0)


def judge_candidate_with_llm(candidate: LinkCandidate, theme: dict) -> Dict[str, float]:
    """Use LLM to judge if a page is a real hidden gem for this theme."""
    fallback_relevance = calculate_relevance_score(candidate, theme)
    lane = classify_source_lane(candidate.url, candidate.title, candidate.description, candidate.concepts)
    focus_hits = theme_focus_hits(candidate, theme)
    lane_gem_bonus = 0.08 if lane in {"old-web", "primary-doc", "enthusiast-research", "museum-object", "indie-essay"} else 0.0
    focus_bonus = min(len(focus_hits) * 0.06, 0.12)
    relevance_bonus = 0.08 if fallback_relevance >= 0.65 else 0.0
    bit_bonus = min(len(candidate.interesting_bits) * 0.03, 0.09)
    fallback = {
        "relevance": fallback_relevance,
        "gem": min(0.92, max(candidate.obscurity_score * 0.62, 0.2) + lane_gem_bonus + focus_bonus + relevance_bonus + bit_bonus),
        "story_seed": min(0.9, 0.25 + (len(candidate.interesting_bits) * 0.12)),
        "anti_corporate": 0.8 if not looks_like_boilerplate(candidate) else 0.2,
        "reason": "fallback heuristic",
    }

    if not API_KEY:
        return fallback
    if OpenAI is None:
        print(f"    LLM link judge unavailable: {OPENAI_IMPORT_ERROR}")
        return fallback

    theme_name = theme.get("name", "")
    links_direction = theme.get("links", theme_name)
    system_prompt = load_link_judge_prompt()
    prompt = f"""Theme: {theme_name}
Links direction: {links_direction}
URL: {candidate.url}
Title: {candidate.title}
Description: {candidate.description}
Top concepts: {", ".join(candidate.concepts[:8])}
Interesting bits: {", ".join(candidate.interesting_bits[:5])}
Content excerpt:
{candidate.content[:1400]}
"""

    try:
        client = OpenAI(
            api_key=API_KEY,
            base_url=API_BASE,
            max_retries=OPENAI_MAX_RETRIES,
            timeout=OPENAI_REQUEST_TIMEOUT,
        )
        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt}
            ],
            temperature=0.1,
            max_tokens=180,
            timeout=OPENAI_REQUEST_TIMEOUT,
        )

        content = strip_thinking_block(response.choices[0].message.content or "")
        match = re.search(r'\{.*\}', content, re.S)
        if not match:
            return fallback
        data = json.loads(match.group(0))
        judged = {
            "relevance": min(max(float(data.get("relevance", fallback["relevance"])), 0.0), 1.0),
            "gem": min(max(float(data.get("gem", fallback["gem"])), 0.0), 1.0),
            "story_seed": min(max(float(data.get("story_seed", fallback["story_seed"])), 0.0), 1.0),
            "anti_corporate": min(max(float(data.get("anti_corporate", fallback["anti_corporate"])), 0.0), 1.0),
            "reason": str(data.get("reason", ""))[:180],
        }
        return judged

    except Exception as e:
        print(f"    LLM link judge failed: {e}")
        return fallback


def get_selection_thresholds(candidate: LinkCandidate, theme: dict) -> Dict[str, float]:
    """Allow a narrow threshold relaxation for trusted, clearly on-theme sources."""
    thresholds = {
        "relevance": MIN_RELEVANCE_SCORE,
        "obscurity": MIN_OBSCURITY_SCORE,
        "gem": MIN_GEM_SCORE,
        "reason": "",
    }

    if not is_trusted_theme_domain(candidate.url, theme):
        return thresholds

    focus_hits = theme_focus_hits(candidate, theme)
    if not focus_hits:
        return thresholds

    lane = classify_source_lane(candidate.url, candidate.title, candidate.description, candidate.concepts)
    if lane not in {"primary-doc", "enthusiast-research", "old-web", "museum-object", "local-history", "niche-institution", "indie-essay"}:
        return thresholds

    thresholds["relevance"] = 0.40
    thresholds["obscurity"] = 0.25
    thresholds["gem"] = 0.48
    thresholds["reason"] = f"trusted {lane} source with {len(focus_hits)} focus hit(s)"
    return thresholds


def score_candidates(candidates: List[LinkCandidate], theme: dict) -> List[LinkCandidate]:
    """Score all candidates for relevance, gem quality, and obscurity."""
    print(f"\nScoring {len(candidates)} candidates...")
    
    for i, candidate in enumerate(candidates, 1):
        print(f"\n  [{i}/{len(candidates)}] {candidate.url}")
        
        judged = judge_candidate_with_llm(candidate, theme)
        candidate.relevance_score = judged["relevance"]
        candidate.gem_score = judged["gem"]
        candidate.story_seed_score = judged["story_seed"]
        candidate.anti_corporate_score = judged["anti_corporate"]
        candidate.curator_reason = judged.get("reason", "")

        candidate.final_score = (
            candidate.relevance_score * 0.30
            + candidate.obscurity_score * 0.20
            + candidate.gem_score * 0.30
            + candidate.story_seed_score * 0.10
            + candidate.anti_corporate_score * 0.10
        )

        print(f"    Relevance: {candidate.relevance_score:.2f}")
        print(f"    Obscurity: {candidate.obscurity_score:.2f}")
        print(f"    Gem: {candidate.gem_score:.2f}")
        print(f"    Story seed: {candidate.story_seed_score:.2f}")
        print(f"    Anti-corporate: {candidate.anti_corporate_score:.2f}")
        print(f"    Final: {candidate.final_score:.2f}")
        if candidate.curator_reason:
            print(f"    Why: {candidate.curator_reason}")
    
    return candidates


def calculate_content_similarity(candidate1: LinkCandidate, candidate2: LinkCandidate) -> float:
    """Calculate similarity between two candidates (0-1, higher = more similar)."""
    # Extract keywords from titles
    def get_keywords(text):
        text = text.lower()
        # Remove common words
        stopwords = {'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for', 'of', 'with', 'by',
                     'that', 'were', 'was', 'are', 'is', 'be', 'been', 'being', 'have', 'has', 'had',
                     'do', 'does', 'did', 'will', 'would', 'could', 'should', 'may', 'might', 'must',
                     'can', 'this', 'these', 'those', 'i', 'you', 'he', 'she', 'it', 'we', 'they'}
        words = re.findall(r'\b[a-z]+\b', text)
        return set(w for w in words if w not in stopwords and len(w) > 3)
    
    title1_keywords = get_keywords(candidate1.title)
    title2_keywords = get_keywords(candidate2.title)
    
    # Title similarity (Jaccard index)
    if title1_keywords and title2_keywords:
        intersection = len(title1_keywords & title2_keywords)
        union = len(title1_keywords | title2_keywords)
        title_sim = intersection / union if union > 0 else 0
    else:
        title_sim = 0
    
    # Concept overlap
    concepts1 = set(c.lower() for c in candidate1.concepts[:5])
    concepts2 = set(c.lower() for c in candidate2.concepts[:5])
    if concepts1 and concepts2:
        concept_overlap = len(concepts1 & concepts2) / min(len(concepts1), len(concepts2))
    else:
        concept_overlap = 0
    
    # Combined similarity (weighted)
    return (title_sim * 0.7) + (concept_overlap * 0.3)


def select_best_links(candidates: List[LinkCandidate], theme: dict, count: int = 7, corpus: Optional[DiscoveryCorpus] = None) -> List[LinkCandidate]:
    """Select the best links based on final score with diversity checks."""
    # Filter out candidates with errors, low scores, or .edu domains
    print(f"\n🔍 Filtering {len(candidates)} candidates:")
    
    # Track filtering reasons
    filter_stats = {
        "errors": 0,
        "disallowed_domain": 0,
        "listicle": 0,
        "bad_page_type": 0,
        "theme_reject": 0,
        "off_theme": 0,
        "low_relevance": 0,
        "low_obscurity": 0,
        "low_gem": 0,
        "edu_domain": 0,
        "boilerplate": 0,
        "trusted_exception": 0,
        "passed": 0
    }
    
    valid = []
    for c in candidates:
        if c.error:
            filter_stats["errors"] += 1
            print(f"  ✗ {c.url[:60]}... - ERROR: {c.error}")
            continue

        domain = urlparse(c.url).netloc
        if is_disallowed_domain(domain):
            filter_stats["disallowed_domain"] += 1
            print(f"  ✗ {c.url[:60]}... - Disallowed domain")
            continue

        if is_listicle_url(c.url, c.title):
            filter_stats["listicle"] += 1
            print(f"  ✗ {c.url[:60]}... - Listicle/junk pattern")
            continue

        bad_page_reason = looks_like_bad_page_type(c)
        if (
            bad_page_reason == "too little substance"
            and (c.concepts or c.interesting_bits or c.description)
        ):
            bad_page_reason = None
        if bad_page_reason:
            filter_stats["bad_page_type"] += 1
            print(f"  ✗ {c.url[:60]}... - Bad page type: {bad_page_reason}")
            continue

        theme_rejection = get_theme_rejection_reason(c, theme)
        if theme_rejection:
            filter_stats["theme_reject"] += 1
            print(f"  ✗ {c.url[:60]}... - {theme_rejection}")
            continue

        if looks_off_theme(c, theme):
            filter_stats["off_theme"] += 1
            print(f"  ✗ {c.url[:60]}... - Off-theme drift")
            continue
        
        if urlparse(c.url).netloc.endswith('.edu') and not is_trusted_theme_domain(c.url, theme):
            filter_stats["edu_domain"] += 1
            print(f"  ✗ {c.url[:60]}... - .edu domain filtered")
            continue

        thresholds = get_selection_thresholds(c, theme)
        used_trusted_exception = bool(
            thresholds["reason"]
            and (
                c.relevance_score < MIN_RELEVANCE_SCORE
                or c.obscurity_score < MIN_OBSCURITY_SCORE
                or c.gem_score < MIN_GEM_SCORE
            )
        )
            
        if c.relevance_score < thresholds["relevance"]:
            filter_stats["low_relevance"] += 1
            print(f"  ✗ {c.url[:60]}... - Low relevance: {c.relevance_score:.2f} < {thresholds['relevance']:.2f}")
            continue
            
        if c.obscurity_score < thresholds["obscurity"]:
            filter_stats["low_obscurity"] += 1
            print(f"  ✗ {c.url[:60]}... - Low obscurity: {c.obscurity_score:.2f} < {thresholds['obscurity']:.2f}")
            continue

        if c.gem_score < thresholds["gem"]:
            filter_stats["low_gem"] += 1
            print(f"  ✗ {c.url[:60]}... - Low gem score: {c.gem_score:.2f} < {thresholds['gem']:.2f}")
            continue

        if looks_like_boilerplate(c):
            filter_stats["boilerplate"] += 1
            print(f"  ✗ {c.url[:60]}... - Looks like boilerplate (contact/privacy/etc.)")
            continue

        if used_trusted_exception:
            filter_stats["trusted_exception"] += 1
            print(f"  ✓ Trusted-source threshold exception: {thresholds['reason']}")
        
        filter_stats["passed"] += 1
        valid.append(c)
    
    print(f"\n📊 Filtering Summary:")
    print(f"  ✓ Passed all filters: {filter_stats['passed']}")
    print(f"  ✗ Errors: {filter_stats['errors']}")
    print(f"  ✗ Disallowed domains: {filter_stats['disallowed_domain']}")
    print(f"  ✗ Listicles/junk: {filter_stats['listicle']}")
    print(f"  ✗ Bad page types: {filter_stats['bad_page_type']}")
    print(f"  ✗ Theme rejects: {filter_stats['theme_reject']}")
    print(f"  ✗ Off-theme drift: {filter_stats['off_theme']}")
    print(f"  ✗ Low relevance: {filter_stats['low_relevance']}")
    print(f"  ✗ Low obscurity: {filter_stats['low_obscurity']}")
    print(f"  ✗ Low gem score: {filter_stats['low_gem']}")
    print(f"  ✗ .edu domains: {filter_stats['edu_domain']}")
    print(f"  ✗ Boilerplate/contact pages: {filter_stats['boilerplate']}")
    print(f"  ✓ Trusted-source exceptions used: {filter_stats['trusted_exception']}")
    
    fallback_mode = False
    if len(valid) < MINIMUM_SELECTED_LINKS:
        print(f"\n⚠️  Only {len(valid)} candidates passed strict filters; enabling fallback thresholds")
        fallback_mode = True
        fallback_tiers = [
            (MIN_RELEVANCE_FALLBACK, MIN_OBSCURITY_FALLBACK, "standard fallback"),
            (0.20, 0.20, "relaxed fallback"),
            (0.15, 0.15, "floor fallback"),
        ]

        for rel_threshold, obs_threshold, label in fallback_tiers:
            if len(valid) >= MINIMUM_SELECTED_LINKS:
                break
            gem_threshold = min(0.45, rel_threshold + 0.05)
            print(f"    • {label}: rel ≥ {rel_threshold:.2f}, obs ≥ {obs_threshold:.2f}, gem ≥ {gem_threshold:.2f}")
            added_this_tier = 0
            for c in candidates:
                if len(valid) >= MINIMUM_SELECTED_LINKS:
                    break
                if c in valid or c.error:
                    continue
                if is_disallowed_domain(urlparse(c.url).netloc):
                    continue
                if get_theme_rejection_reason(c, theme):
                    continue
                if looks_off_theme(c, theme):
                    continue
                if looks_like_boilerplate(c):
                    continue
                bad_page_reason = looks_like_bad_page_type(c)
                if (
                    bad_page_reason == "too little substance"
                    and (c.concepts or c.interesting_bits or c.description)
                ):
                    bad_page_reason = None
                if bad_page_reason:
                    continue
                if is_listicle_url(c.url, c.title):
                    continue
                if c.relevance_score >= rel_threshold and c.obscurity_score >= obs_threshold and c.gem_score >= gem_threshold:
                    print(f"  ➕ Fallback candidate: {c.url[:60]}... (rel {c.relevance_score:.2f}, obs {c.obscurity_score:.2f}, gem {c.gem_score:.2f})")
                    valid.append(c)
                    added_this_tier += 1
            if added_this_tier:
                print(f"    → Added {added_this_tier} candidate(s) via {label}")

    if not valid:
        print("\n❌ No candidates passed filtering!")
        return []
    
    # Sort by final score adjusted for recent repetition in the persistent corpus
    scored_valid = []
    for candidate in valid:
        novelty_adjustment = corpus.novelty_adjustment(candidate) if corpus else 0.0
        selection_score = candidate.final_score + novelty_adjustment
        setattr(candidate, "selection_score", selection_score)
        setattr(candidate, "novelty_adjustment", novelty_adjustment)
        scored_valid.append(candidate)

    sorted_candidates = sorted(scored_valid, key=lambda x: getattr(x, "selection_score", x.final_score), reverse=True)
    
    # Select top N with diversity checks (domain + content)
    selected = []
    domains_used = []
    lanes_used = []
    skipped_domain = 0
    skipped_lane = 0
    skipped_similar = 0
    
    lane_limit = 2 if len(valid) > MINIMUM_SELECTED_LINKS else 3

    for candidate in sorted_candidates:
        if len(selected) >= count:
            break
        
        domain = urlparse(candidate.url).netloc
        lane = classify_source_lane(candidate.url, candidate.title, candidate.description, candidate.concepts)
        
        # Skip if domain already used (max 3 links per domain)
        if domains_used.count(domain) >= 3:
            print(f"  ⏭ Skipping (domain limit reached): {candidate.title[:50]}...")
            skipped_domain += 1
            continue

        if lanes_used.count(lane) >= lane_limit:
            print(f"  ⏭ Skipping (lane limit reached: {lane}): {candidate.title[:50]}...")
            skipped_lane += 1
            continue
        
        # Check content similarity with already selected links
        is_duplicate = False
        for existing in selected:
            similarity = calculate_content_similarity(candidate, existing)
            if similarity > 0.5:  # Skip if >50% similar
                print(f"  ⏭ Skipping (similarity {similarity:.0%} to '{existing.title[:40]}...'): {candidate.title[:40]}...")
                skipped_similar += 1
                is_duplicate = True
                break
        
        if is_duplicate:
            continue
        
        selected.append(candidate)
        domains_used.append(domain)
        lanes_used.append(lane)
        if corpus:
            print(
                f"  ✓ Selected: {candidate.title[:50]}... "
                f"(base: {candidate.final_score:.2f}, novelty: {getattr(candidate, 'novelty_adjustment', 0.0):+.2f}, "
                f"selection: {getattr(candidate, 'selection_score', candidate.final_score):.2f})"
            )
        else:
            print(f"  ✓ Selected: {candidate.title[:50]}... (score: {candidate.final_score:.2f})")
    
    print(f"\n📊 Selection Summary:")
    print(f"  ✓ Selected: {len(selected)}")
    print(f"  ⏭ Skipped (domain limit): {skipped_domain}")
    print(f"  ⏭ Skipped (lane limit): {skipped_lane}")
    print(f"  ⏭ Skipped (similar content): {skipped_similar}")
    
    return selected


def _extract_summary_text(candidate: LinkCandidate) -> str:
    """Build cleaner summaries, preferring descriptions and sentence boundaries."""
    description = (candidate.description or "").strip()
    if description and len(description) >= 40:
        base = description
    else:
        content = (candidate.content or "").replace('\n', ' ').strip()
        if not content:
            domain = urlparse(candidate.url).netloc
            if candidate.url.lower().endswith('.pdf') or 'application/pdf' in description.lower():
                return f"PDF download hosted by {domain}."
            return f"Resource hosted on {domain}—open to explore the primary source."
        sentences = re.split(r'(?<=[.!?])\s+', content)
        base = ' '.join(sentences[:2]).strip()
    if not base:
        base = "No summary available yet—worth exploring directly."
    if len(base) > 320:
        base = base[:317].rstrip() + "..."
    return base


def _generate_tags(candidate: LinkCandidate) -> List[str]:
    """Create short tags using obscurity score and top concepts."""
    tags = [f"obs:{candidate.obscurity_score:.2f}"]
    for concept in candidate.concepts[:3]:
        slug = re.sub(r'[^a-z0-9]+', '-', concept.lower()).strip('-')
        if slug and slug not in tags:
            tags.append(slug[:24])
    return tags


def generate_summary(candidate: LinkCandidate, theme: dict) -> Tuple[str, str, List[str]]:
    """Generate title, cleaned summary, and tags for a link."""
    title = candidate.title.strip() or candidate.url
    if len(title) > 120:
        title = title[:117] + "..."
    summary = _extract_summary_text(candidate)
    tags = _generate_tags(candidate)
    return title, summary, tags


def build_story_link_context(links: List[LinkCandidate], theme: dict, target_date: Optional[datetime] = None) -> dict:
    """Build a compact JSON artifact the story generator can use for same-day inspiration."""
    today = target_date or datetime.now()
    motifs = []
    interesting = []
    selected_links = []
    seen = set()

    for link in links[:5]:
        title = (link.title or "").strip()
        reason = (link.curator_reason or "").strip()
        concepts = [c for c in link.concepts[:4] if c]
        bits = [b for b in link.interesting_bits[:3] if b]
        selected_links.append({
            "title": title,
            "url": link.url,
            "concepts": concepts,
            "reason": reason,
        })
        for item in concepts + bits:
            key = item.lower()
            if key in seen:
                continue
            seen.add(key)
            if len(item) <= 60:
                motifs.append(item)
        for item in bits:
            if len(item) <= 120 and item not in interesting:
                interesting.append(item)

    return {
        "date": today.strftime("%Y-%m-%d"),
        "theme": theme.get("name", "unknown"),
        "links_direction": theme.get("links", theme.get("name", "unknown")),
        "motifs": motifs[:12],
        "interesting_bits": interesting[:8],
        "links": selected_links,
    }


def save_story_link_context(links: List[LinkCandidate], theme: dict, target_date: Optional[datetime] = None) -> Path:
    """Persist selected-link context for the story generator."""
    context = build_story_link_context(links, theme, target_date)
    date_str = context["date"]
    filepath = STORY_CONTEXT_DIR / f"{date_str}-links.json"
    filepath.write_text(json.dumps(context, indent=2))
    print(f"✓ Saved story context to: {filepath}")
    return filepath


def save_links(links: List[LinkCandidate], theme: dict, target_date: Optional[datetime] = None) -> Path:
    """Save the selected links as a markdown file."""
    today = target_date or datetime.now()
    date_str = today.strftime("%Y-%m-%d")
    theme_name = theme.get("name", "unknown")
    try:
        commit_hash = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        commit_url = f"https://github.com/obscurebit/b1ts/tree/{commit_hash}"
    except Exception:
        commit_hash = "unknown"
        commit_url = "#"

    # Ensure posts directory exists
    posts_dir = links_posts_output_dir()
    posts_dir.mkdir(parents=True, exist_ok=True)
    
    # Create filename
    filename = f"{date_str}-daily-links.md"
    filepath = posts_dir / filename
    
    # Build markdown content
    links_content = ""
    for i, link in enumerate(links, 1):
        title, summary, tags = generate_summary(link, theme)
        url = link.url
        tag_line = ', '.join(f"`{tag}`" for tag in tags) if tags else "``"
        
        links_content += f"""
## {i}. {title}

{summary}

<p class="link-tags">Tags: {tag_line}</p>

<a href="{url}" target="_blank" rel="noopener" class="visit-link">Visit Link →</a>

---
"""
    
    frontmatter = f"""---
date: {today}
title: "Obscure Links - {today.strftime('%B %d, %Y')}"
description: "Today's curated obscure links: {theme_name}"
author: "Obscure Bit"
theme: "{theme_name}"
---

"""
    
    content = f"""{frontmatter}
# Obscure Links - {today.strftime('%B %d, %Y')}

**Theme: {theme_name.title()}**

Today's curated discoveries from the hidden corners of the web.

{links_content}

<div style="display: flex; justify-content: space-between; align-items: center; margin-top: 2rem;">
  <button class="share-btn" data-url="{{% raw %}}{{{{ page.canonical_url }}}}{{% endraw %}}" data-title="Obscure Links - {date_str}">
    Share today's links
  </button>
  <a href="{commit_url}" target="_blank" rel="noopener" class="story-gen-link">
    gen:{commit_hash}
  </a>
</div>
"""
    
    filepath.write_text(content)
    print(f"\n✓ Saved {len(links)} links to: {filepath}")
    return filepath


def main():
    """Main entry point for link generation."""
    args = parse_args()
    target_date = resolve_date(args.date) if args.date else None
    theme_override = load_theme_override(args.theme_json)
    theme = theme_override or get_daily_theme(target_date)
    theme_name = theme.get("name", "unknown")
    print(f"\nTheme: {theme_name}")
    print(f"Direction: {theme.get('links', theme_name)}")

    print("=" * 70)
    print("Obscure Bit - Link Generation v3")
    print("=" * 70)
    
    if not API_KEY:
        print("Error: OPENAI_API_KEY environment variable not set")
        sys.exit(1)

    # Load persistent URL registry for cross-day dedup
    registry = LinkRegistry()
    corpus = DiscoveryCorpus()
    print(f"🗂️  Registry loaded: {registry.total_links} previously-published links")
    print(f"🗂️  Discovery corpus loaded: {len(corpus.candidates)} candidates, {len(corpus.selection_history)} selections")
    existing_pool = corpus.candidate_pool(
        LinkCandidate,
        limit=80,
        include_published=False,
        theme_name=theme_name,
    )
    
    # Step 1: Get candidate URLs
    print("\n" + "=" * 70)
    print("STEP 1: Finding Candidate URLs")
    print("=" * 70)
    candidates_urls = get_candidate_urls(theme, registry=registry)
    
    if not candidates_urls:
        if not existing_pool:
            print("Error: No candidate URLs found and no stored corpus candidates available")
            sys.exit(1)
        print("⚠️  No fresh candidate URLs found; continuing with stored corpus only")
        candidates = []
    else:
        # Step 2: Scrape and analyze
        print("\n" + "=" * 70)
        print("STEP 2: Scraping Content")
        print("=" * 70)
        candidates = scrape_and_analyze(candidates_urls, theme)
    
    valid_candidates = [c for c in candidates if c.error is None]
    print(f"\nSuccessfully scraped {len(valid_candidates)}/{len(candidates)} URLs")

    if len(valid_candidates) < MINIMUM_SELECTED_LINKS:
        print(
            f"⚠️  Only {len(valid_candidates)} newly scraped candidates; "
            f"stored corpus has {len(existing_pool)} unpublished candidate(s) for this theme"
        )
        if not valid_candidates and not existing_pool:
            print("Error: No viable candidates available from scrape or corpus")
            sys.exit(1)
    
    # Step 3: Score candidates
    print("\n" + "=" * 70)
    print("STEP 3: Scoring Relevance & Obscurity")
    print("=" * 70)
    scored = score_candidates(valid_candidates, theme)
    
    # Step 4: Select best
    print("\n" + "=" * 70)
    print("STEP 4: Updating Discovery Corpus")
    print("=" * 70)
    date_str = (target_date or datetime.now()).strftime("%Y-%m-%d")
    for candidate in scored:
        corpus.upsert_candidate(candidate, theme_name, date_str)
    corpus.save()
    print(f"✓ Corpus updated with {len(scored)} scored candidates")

    print("\n" + "=" * 70)
    print("STEP 5: Selecting Best Links")
    print("=" * 70)
    selection_pool = corpus.candidate_pool(
        LinkCandidate,
        limit=80,
        include_published=False,
        theme_name=theme_name,
    )
    print(f"Selection pool size from corpus: {len(selection_pool)}")
    selected = select_best_links(selection_pool, theme, count=7, corpus=corpus)
    
    if not selected:
        print("Error: No links passed scoring thresholds")
        sys.exit(1)

    if len(selected) < MINIMUM_SELECTED_LINKS:
        print(f"Error: Only {len(selected)} link(s) selected; minimum required is {MINIMUM_SELECTED_LINKS}")
        sys.exit(1)
    
    print(f"\n✓ Selected {len(selected)} links")
    
    # Step 6: Save
    print("\n" + "=" * 70)
    print("STEP 6: Saving Results")
    print("=" * 70)
    today = target_date or datetime.now()
    date_str = today.strftime("%Y-%m-%d")
    filepath = save_links(selected, theme, target_date)
    save_story_link_context(selected, theme, target_date)

    # Step 7: Persist nightly selection history
    corpus.mark_selected(selected, theme_name, date_str)
    corpus.save()

    # Step 8: Register published URLs in the persistent registry
    registry.register_batch(
        [(link.url, link.title) for link in selected],
        date=date_str,
        theme=theme_name,
    )
    stats = registry.stats()
    print(f"\n🗂️  Registry updated: {stats['total_links']} total links across {stats['days_tracked']} days")
    
    print("\n" + "=" * 70)
    print("SUCCESS!")
    print("=" * 70)
    print(f"Generated {len(selected)} curated links")
    print(f"Saved to: {filepath}")
    
    return selected, theme


if __name__ == "__main__":
    main()
