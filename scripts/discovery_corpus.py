#!/usr/bin/env python3
"""
Repo-backed discovery corpus for Obscure Bit.

This stores compact structured memory in the repository so nightly runs can:
- remember previously discovered pages
- avoid repetitive domains/concepts
- curate from a growing candidate pool instead of only tonight's search results
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional
from urllib.parse import urlparse

from link_registry import normalize_url


DATA_DIR = Path("data/discovery")
CANDIDATES_PATH = DATA_DIR / "candidates.jsonl"
SELECTION_HISTORY_PATH = DATA_DIR / "selection_history.jsonl"
DOMAIN_STATE_PATH = DATA_DIR / "domain_state.json"
STORY_CONTEXT_DIR = DATA_DIR / "story_context"
LINK_POSTS_DIR = Path("docs/links/posts")


def ensure_data_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    STORY_CONTEXT_DIR.mkdir(parents=True, exist_ok=True)


def _safe_domain(url: str) -> str:
    try:
        domain = urlparse(url).netloc.lower().rstrip(".")
        if domain.startswith("www."):
            domain = domain[4:]
        return domain
    except Exception:
        return ""


def _dedupe_keep_order(items: Iterable[str], limit: int) -> List[str]:
    seen = set()
    result = []
    for item in items:
        if not item:
            continue
        key = item.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(item.strip())
        if len(result) >= limit:
            break
    return result


def classify_source_lane(url: str, title: str = "", description: str = "", concepts: Optional[List[str]] = None) -> str:
    domain = _safe_domain(url)
    text = " ".join([
        url.lower(),
        (title or "").lower(),
        (description or "").lower(),
        " ".join((concepts or [])).lower(),
    ])

    if any(token in domain for token in ["museum", "gallery", "collection", "si.edu"]):
        return "museum-object"
    if any(token in domain for token in ["untappedcities.com", "roadsideamerica.com", "forgotten-ny.com", "nycsubway.org", "subbrit.org.uk"]):
        return "local-history"
    if any(token in text for token in ["oral history", "local history", "historical society", "community archive"]):
        return "local-history"
    if any(token in domain for token in ["textfiles.com", "404pagefound.com", "lostmediawiki.com"]):
        return "old-web"
    if any(token in text for token in ["old web", "webring", "bbs", "homepage", "personal site", "gopher"]):
        return "old-web"
    if any(token in text for token in ["manual", "logbook", "field notes", "field report", "catalog", "correspondence", "lab notes", "notebook"]):
        return "primary-doc"
    if any(token in text for token in ["forum", "enthusiast", "restoration", "collector", "fan page", "independent research"]):
        return "enthusiast-research"
    if any(token in domain for token in ["blog", "weblog"]) or any(token in text for token in ["essay", "notes", "journal"]):
        return "indie-essay"
    if domain.endswith(".gov") or domain.endswith(".org"):
        return "niche-institution"
    return "general-obscure"


@dataclass
class CorpusCandidate:
    normalized_url: str
    url: str
    domain: str
    title: str
    description: str
    concepts: List[str]
    interesting_bits: List[str]
    lane: str
    page_type: str
    theme_hint: str
    first_seen: str
    last_seen: str
    last_scored: str
    relevance_score: float
    obscurity_score: float
    gem_score: float
    story_seed_score: float
    anti_corporate_score: float
    final_score: float
    curator_reason: str
    selected_count: int = 0
    last_selected: str = ""
    is_published: bool = False
    dead: bool = False


class DiscoveryCorpus:
    def __init__(
        self,
        candidates_path: Path = CANDIDATES_PATH,
        selection_history_path: Path = SELECTION_HISTORY_PATH,
        domain_state_path: Path = DOMAIN_STATE_PATH,
    ):
        ensure_data_dirs()
        self.candidates_path = candidates_path
        self.selection_history_path = selection_history_path
        self.domain_state_path = domain_state_path
        self.candidates: Dict[str, CorpusCandidate] = {}
        self.selection_history: List[Dict] = []
        self.domain_state: Dict[str, Dict] = {}
        self._load()
        self._bootstrap_from_published_posts_if_needed()

    def _load_jsonl(self, path: Path) -> List[Dict]:
        if not path.exists():
            return []
        rows = []
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return rows

    def _load(self) -> None:
        for row in self._load_jsonl(self.candidates_path):
            try:
                candidate = CorpusCandidate(**row)
                self.candidates[candidate.normalized_url] = candidate
            except TypeError:
                continue
        self.selection_history = self._load_jsonl(self.selection_history_path)
        if self.domain_state_path.exists():
            try:
                self.domain_state = json.loads(self.domain_state_path.read_text())
            except json.JSONDecodeError:
                self.domain_state = {}

    def save(self) -> None:
        ensure_data_dirs()
        candidate_lines = [
            json.dumps(asdict(candidate), sort_keys=True)
            for candidate in sorted(self.candidates.values(), key=lambda item: item.normalized_url)
        ]
        self.candidates_path.write_text("\n".join(candidate_lines) + ("\n" if candidate_lines else ""))

        history_lines = [
            json.dumps(entry, sort_keys=True)
            for entry in sorted(self.selection_history, key=lambda item: (item.get("date", ""), item.get("url", "")))
        ]
        self.selection_history_path.write_text("\n".join(history_lines) + ("\n" if history_lines else ""))
        self.domain_state_path.write_text(json.dumps(self.domain_state, indent=2, sort_keys=True))

    def _bootstrap_from_published_posts_if_needed(self) -> None:
        if self.selection_history or not LINK_POSTS_DIR.exists():
            return

        for path in sorted(LINK_POSTS_DIR.glob("*.md")):
            text = path.read_text()
            date = path.name[:10]
            theme_match = re.search(r'^theme:\s+"([^"]+)"', text, re.MULTILINE)
            theme = theme_match.group(1) if theme_match else "unknown"
            for block in re.findall(r"##\s+\d+\.\s+(.+?)\n.*?<a href=\"([^\"]+)\"", text, re.S):
                title, url = block
                normalized = normalize_url(url)
                domain = _safe_domain(url)
                lane = classify_source_lane(url, title)
                entry = {
                    "date": date,
                    "theme": theme,
                    "url": url,
                    "normalized_url": normalized,
                    "domain": domain,
                    "title": title.strip(),
                    "lane": lane,
                    "concepts": [],
                }
                self.selection_history.append(entry)
                self.domain_state.setdefault(domain, {"last_selected": "", "selected_count": 0, "last_seen": ""})
                self.domain_state[domain]["last_selected"] = date
                self.domain_state[domain]["selected_count"] += 1

        if self.selection_history:
            self.save()

    def upsert_candidate(self, candidate, theme_name: str, date_str: str) -> None:
        normalized = normalize_url(candidate.url)
        domain = _safe_domain(candidate.url)
        existing = self.candidates.get(normalized)
        lane = classify_source_lane(candidate.url, candidate.title, candidate.description, candidate.concepts)
        page_type = "artifact-page"

        payload = CorpusCandidate(
            normalized_url=normalized,
            url=candidate.url,
            domain=domain,
            title=(candidate.title or "")[:220],
            description=(candidate.description or "")[:400],
            concepts=_dedupe_keep_order(candidate.concepts[:8], 8),
            interesting_bits=_dedupe_keep_order(candidate.interesting_bits[:6], 6),
            lane=lane,
            page_type=page_type,
            theme_hint=theme_name,
            first_seen=existing.first_seen if existing else date_str,
            last_seen=date_str,
            last_scored=date_str,
            relevance_score=float(candidate.relevance_score),
            obscurity_score=float(candidate.obscurity_score),
            gem_score=float(getattr(candidate, "gem_score", 0.0)),
            story_seed_score=float(getattr(candidate, "story_seed_score", 0.0)),
            anti_corporate_score=float(getattr(candidate, "anti_corporate_score", 0.0)),
            final_score=float(candidate.final_score),
            curator_reason=(getattr(candidate, "curator_reason", "") or "")[:220],
            selected_count=existing.selected_count if existing else 0,
            last_selected=existing.last_selected if existing else "",
            is_published=existing.is_published if existing else False,
            dead=False,
        )
        self.candidates[normalized] = payload
        self.domain_state.setdefault(domain, {"last_selected": "", "selected_count": 0, "last_seen": ""})
        self.domain_state[domain]["last_seen"] = date_str

    def mark_selected(self, candidates: List, theme_name: str, date_str: str) -> None:
        for candidate in candidates:
            normalized = normalize_url(candidate.url)
            corpus_candidate = self.candidates.get(normalized)
            if corpus_candidate:
                corpus_candidate.selected_count += 1
                corpus_candidate.last_selected = date_str
                corpus_candidate.is_published = True

            entry = {
                "date": date_str,
                "theme": theme_name,
                "url": candidate.url,
                "normalized_url": normalized,
                "domain": _safe_domain(candidate.url),
                "title": (candidate.title or "")[:220],
                "lane": classify_source_lane(candidate.url, candidate.title, candidate.description, candidate.concepts),
                "concepts": _dedupe_keep_order(candidate.concepts[:6], 6),
            }
            self.selection_history.append(entry)

            domain = entry["domain"]
            self.domain_state.setdefault(domain, {"last_selected": "", "selected_count": 0, "last_seen": ""})
            self.domain_state[domain]["last_selected"] = date_str
            self.domain_state[domain]["selected_count"] += 1

    def hydrate_candidate(self, candidate_cls, record: CorpusCandidate):
        candidate = candidate_cls(record.url, record.title, record.description)
        candidate.content = record.description
        candidate.concepts = list(record.concepts)
        candidate.interesting_bits = list(record.interesting_bits)
        candidate.relevance_score = record.relevance_score
        candidate.obscurity_score = record.obscurity_score
        candidate.gem_score = record.gem_score
        candidate.story_seed_score = record.story_seed_score
        candidate.anti_corporate_score = record.anti_corporate_score
        candidate.final_score = record.final_score
        candidate.curator_reason = record.curator_reason
        return candidate

    def recent_history(self, days: int = 21) -> List[Dict]:
        if not self.selection_history:
            return []
        sorted_history = sorted(self.selection_history, key=lambda item: item.get("date", ""))
        return sorted_history[-days * 7:]

    def _recent_domains(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for entry in self.recent_history():
            domain = entry.get("domain", "")
            if domain:
                counts[domain] = counts.get(domain, 0) + 1
        return counts

    def _recent_lanes(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for entry in self.recent_history():
            lane = entry.get("lane", "")
            if lane:
                counts[lane] = counts.get(lane, 0) + 1
        return counts

    def _recent_concepts(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for entry in self.recent_history():
            for concept in entry.get("concepts", []):
                key = concept.lower()
                counts[key] = counts.get(key, 0) + 1
        return counts

    def novelty_adjustment(self, candidate) -> float:
        """Return a signed score adjustment based on recent usage."""
        domain_counts = self._recent_domains()
        lane_counts = self._recent_lanes()
        concept_counts = self._recent_concepts()

        penalty = 0.0
        domain = _safe_domain(candidate.url)
        lane = classify_source_lane(candidate.url, candidate.title, candidate.description, candidate.concepts)

        penalty -= min(domain_counts.get(domain, 0) * 0.08, 0.24)
        penalty -= min(lane_counts.get(lane, 0) * 0.05, 0.15)

        overlap = sum(concept_counts.get(concept.lower(), 0) for concept in candidate.concepts[:4])
        penalty -= min(overlap * 0.02, 0.18)

        if getattr(candidate, "gem_score", 0.0) >= 0.8 and getattr(candidate, "story_seed_score", 0.0) >= 0.7:
            penalty += 0.04

        return penalty

    def candidate_pool(
        self,
        candidate_cls,
        limit: int = 80,
        include_published: bool = False,
        theme_name: Optional[str] = None,
    ) -> List:
        records = sorted(self.candidates.values(), key=lambda item: item.final_score, reverse=True)
        result = []
        for record in records:
            if record.dead:
                continue
            if record.is_published and not include_published:
                continue
            if theme_name and record.theme_hint and record.theme_hint != theme_name:
                continue
            result.append(self.hydrate_candidate(candidate_cls, record))
            if len(result) >= limit:
                break
        return result
