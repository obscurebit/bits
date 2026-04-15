#!/usr/bin/env python3
"""
Persistent URL registry for Obscure Bit link deduplication.

Maintains a SHA-256 hash index of every URL ever published, preventing
cross-day duplicates. Also tracks per-domain frequency for global
diversity enforcement.

Registry file: data/discovery/link_registry.json
"""

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse, urlencode, parse_qs, urlunparse


REGISTRY_PATH = Path("data/discovery/link_registry.json")
LEGACY_REGISTRY_PATH = Path("cache/link_registry.json")

# Query parameters that are tracking/noise — strip before hashing
STRIP_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "ref", "fbclid", "gclid", "mc_cid", "mc_eid", "msclkid", "twclid",
    "s", "source", "via", "share", "curator", "trk",
}


def normalize_url(url: str) -> str:
    """Normalize a URL for consistent hashing.

    - Lowercase scheme + domain
    - Strip trailing slashes from path
    - Remove tracking query params
    - Sort remaining query params
    - Remove fragment
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return url.strip().lower()

    scheme = (parsed.scheme or "https").lower()
    netloc = parsed.netloc.lower().rstrip(".")
    # Remove www. prefix for consistency
    if netloc.startswith("www."):
        netloc = netloc[4:]
    path = parsed.path.rstrip("/") or "/"

    # Filter and sort query params
    params = parse_qs(parsed.query, keep_blank_values=False)
    filtered = {
        k: sorted(v) for k, v in params.items()
        if k.lower() not in STRIP_PARAMS
    }
    query = urlencode(filtered, doseq=True) if filtered else ""

    return urlunparse((scheme, netloc, path, "", query, ""))


def url_hash(url: str) -> str:
    """SHA-256 hex digest of the normalized URL."""
    normalized = normalize_url(url)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _domain_from_url(url: str) -> str:
    try:
        netloc = urlparse(url).netloc.lower().rstrip(".")
        if netloc.startswith("www."):
            netloc = netloc[4:]
        return netloc
    except Exception:
        return ""


class LinkRegistry:
    """Persistent registry of published link URLs."""

    def __init__(self, path: Optional[Path] = None):
        self.path = path or REGISTRY_PATH
        self.data: Dict = {"version": 1, "links": {}}
        self._load()

    # ── persistence ──────────────────────────────────────────────

    def _load(self) -> None:
        if not self.path.exists() and LEGACY_REGISTRY_PATH.exists():
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.path.write_text(LEGACY_REGISTRY_PATH.read_text())
            except OSError:
                pass

        if self.path.exists():
            try:
                self.data = json.loads(self.path.read_text())
            except (json.JSONDecodeError, OSError):
                self.data = {"version": 1, "links": {}}
        else:
            self.data = {"version": 1, "links": {}}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=2, sort_keys=False))

    # ── lookup ───────────────────────────────────────────────────

    def contains(self, url: str) -> bool:
        """Return True if this URL (normalized) has been published before."""
        h = url_hash(url)
        return h in self.data["links"]

    def lookup(self, url: str) -> Optional[Dict]:
        """Return the registry entry for a URL, or None."""
        h = url_hash(url)
        return self.data["links"].get(h)

    def domain_count(self, domain: str) -> int:
        """Count how many times a domain has appeared across all days."""
        domain = domain.lower().lstrip("www.")
        return sum(
            1 for entry in self.data["links"].values()
            if entry.get("domain", "") == domain
        )

    # ── registration ─────────────────────────────────────────────

    def register(self, url: str, date: str, theme: str, title: str = "") -> None:
        """Add a URL to the registry."""
        h = url_hash(url)
        self.data["links"][h] = {
            "url": url,
            "domain": _domain_from_url(url),
            "date": date,
            "theme": theme,
            "title": title[:120] if title else "",
        }

    def register_batch(
        self,
        urls: List[Tuple[str, str]],
        date: str,
        theme: str,
    ) -> None:
        """Register multiple (url, title) pairs at once."""
        for url, title in urls:
            self.register(url, date, theme, title)
        self.save()

    # ── filtering helper ─────────────────────────────────────────

    def filter_new(self, urls: List[str]) -> Tuple[List[str], int]:
        """Return (new_urls, num_rejected) — only URLs not in the registry."""
        new = []
        rejected = 0
        for url in urls:
            if self.contains(url):
                rejected += 1
            else:
                new.append(url)
        return new, rejected

    # ── stats ────────────────────────────────────────────────────

    @property
    def total_links(self) -> int:
        return len(self.data["links"])

    def stats(self) -> Dict:
        """Return summary statistics."""
        domains: Dict[str, int] = {}
        dates: set = set()
        for entry in self.data["links"].values():
            d = entry.get("domain", "unknown")
            domains[d] = domains.get(d, 0) + 1
            dates.add(entry.get("date", ""))
        return {
            "total_links": self.total_links,
            "unique_domains": len(domains),
            "days_tracked": len(dates),
            "top_domains": sorted(domains.items(), key=lambda x: -x[1])[:10],
        }
