#!/usr/bin/env python3
"""
Web scraping and content analysis utilities for finding obscure links.

This module provides tools to:
- Fetch and validate web content
- Extract key concepts and themes
- Score obscurity of content
- Cache findings for story generation
"""

import os
import re
import json
import time
import hashlib
from collections import Counter
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse, unquote, urlunparse
from pathlib import Path
from dataclasses import dataclass, asdict
import requests
from bs4 import BeautifulSoup, Comment

try:
    import certifi
except Exception:
    certifi = None

# Cache directory
CACHE_DIR = Path("cache/web_content")
CACHE_DIR.mkdir(parents=True, exist_ok=True)

FETCH_MAX_RETRIES = 3
FETCH_DISABLE_AFTER_FAILURES = 4
FETCH_RETRYABLE_STATUSES = {408, 425, 429, 500, 502, 503, 504}
TRANSIENT_ERROR_MARKERS = (
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

@dataclass
class ScrapedContent:
    """Data structure for scraped web content."""
    url: str
    title: str
    description: str
    content: str
    concepts: List[str]
    obscurity_score: float
    accessibility_score: float
    interesting_bits: List[str]
    error: Optional[str] = None

class WebScraper:
    """Enhanced web scraper with content analysis."""
    
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        })
        if certifi:
            try:
                ca_bundle = certifi.where()
                if ca_bundle and Path(ca_bundle).exists():
                    self.session.verify = ca_bundle
            except Exception:
                pass
        self.transient_fetch_failures = 0
        self.network_disabled = False
        
        # Obscurity indicators
        self.obscurity_indicators = {
            'high': [
                'pdf', 'ftp://', 'gopher://', 'telnet://', 'mailto:',
                '.edu', '.gov', 'archive.org', 'wayback machine',
                'database', 'repository', 'collection', 'archive',
                'manuscript', 'rare', 'historical', 'ancient',
                'obscure', 'forgotten', 'hidden', 'lost',
                'unpublished', 'underground', 'alternative'
            ],
            'medium': [
                'blog', 'personal', 'independent', 'alternative',
                'niche', 'specialized', 'academic', 'research',
                'technical', 'documentation', 'specification'
            ],
            'low': [
                'wikipedia.org', 'github.com', 'stackoverflow.com',
                'medium.com', 'substack.com', 'news', 'media'
            ]
        }
        
        # Interesting content patterns
        self.interesting_patterns = [
            r'\d{4}[-/]\d{1,2}[-/]\d{1,2}',  # Dates
            r'\$[\d,]+',  # Money
            r'\b\d{1,3}\b%',  # Percentages
            r'[A-Z]{2,}\d+',  # Codes like NASA42
            r'"[^"]{30,}"',  # Long quotes
            r'\b\w{20,}\b',  # Long words
        ]
    
    def get_cache_path(self, url: str) -> Path:
        """Get cache file path for URL."""
        url_hash = hashlib.md5(url.encode()).hexdigest()
        return CACHE_DIR / f"{url_hash}.json"
    
    def load_from_cache(self, url: str) -> Optional[ScrapedContent]:
        """Load content from cache if available."""
        cache_path = self.get_cache_path(url)
        if cache_path.exists():
            try:
                with open(cache_path, 'r') as f:
                    data = json.load(f)
                return ScrapedContent(**data)
            except:
                pass
        return None
    
    def save_to_cache(self, content: ScrapedContent):
        """Save content to cache."""
        cache_path = self.get_cache_path(content.url)
        with open(cache_path, 'w') as f:
            json.dump(asdict(content), f, indent=2)

    def _is_transient_request_error(self, error: Exception) -> bool:
        message = str(error).lower()
        return any(marker in message for marker in TRANSIENT_ERROR_MARKERS)

    def _request_variants(self, url: str) -> List[str]:
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
    
    def fetch_page(self, url: str, timeout: int = 10) -> Optional[requests.Response]:
        """Fetch a web page with retries for transient DNS/connection failures."""
        if self.network_disabled:
            return None
        last_error: Optional[Exception] = None
        variants = self._request_variants(url)

        for attempt in range(FETCH_MAX_RETRIES):
            for candidate_url in variants:
                try:
                    response = self.session.get(candidate_url, timeout=timeout, allow_redirects=True)
                    if response.status_code in FETCH_RETRYABLE_STATUSES:
                        last_error = requests.HTTPError(f"retryable status {response.status_code}")
                        continue
                    response.raise_for_status()
                    self.transient_fetch_failures = 0
                    return response
                except requests.RequestException as error:
                    last_error = error
                    if not self._is_transient_request_error(error):
                        return None

            if attempt < FETCH_MAX_RETRIES - 1:
                time.sleep(1.5 * (attempt + 1))

        if last_error and self._is_transient_request_error(last_error):
            self.transient_fetch_failures += 1
            if not self.network_disabled and self.transient_fetch_failures >= FETCH_DISABLE_AFTER_FAILURES:
                self.network_disabled = True
                print("    ⚠️  Disabling live page fetches for the remainder of this run")
        return None

    def _derive_asset_title(self, url: str) -> str:
        path = unquote(urlparse(url).path)
        basename = path.rstrip('/').split('/')[-1]
        if basename:
            basename = re.sub(r'\.[A-Za-z0-9]+$', '', basename)
            cleaned = re.sub(r'[-_]+', ' ', basename).strip()
            if cleaned:
                return cleaned.title()
        domain = urlparse(url).netloc
        return domain or url

    def _handle_non_html_asset(self, url: str, response: requests.Response, asset_type: str) -> ScrapedContent:
        title = self._derive_asset_title(url)
        domain = urlparse(url).netloc
        description = f"{asset_type} document from {domain}. Download to explore the primary source."
        concepts = [title] if title else []
        result = ScrapedContent(
            url=url,
            title=title,
            description=description,
            content="",
            concepts=concepts,
            obscurity_score=self.calculate_obscurity_score(url, title, description),
            accessibility_score=1.0 if response.status_code == 200 else 0.0,
            interesting_bits=[f"{asset_type} download"],
        )
        self.save_to_cache(result)
        return result

    def _infer_asset_type(self, url: str, content_type: str) -> Optional[str]:
        lowered = (content_type or "").lower()
        url_lower = url.lower()
        if "application/pdf" in lowered or url_lower.endswith(".pdf"):
            return "PDF"
        if "application/epub+zip" in lowered or url_lower.endswith(".epub"):
            return "EPUB"
        if "application/x-mobipocket-ebook" in lowered or url_lower.endswith(".mobi"):
            return "MOBI"
        if "application/zip" in lowered or url_lower.endswith(".zip"):
            return "ZIP"
        if "application/octet-stream" in lowered:
            return "binary asset"
        if lowered and "html" not in lowered and "xml" not in lowered and "text/" not in lowered:
            return lowered.split(";")[0]
        return None
    
    def extract_text(self, soup: BeautifulSoup) -> str:
        """Extract clean text from HTML."""
        # Remove script, style, and comment elements
        for element in soup(['script', 'style', 'nav', 'footer', 'header', 'aside']):
            element.decompose()
        
        # Remove comments
        for comment in soup.find_all(string=lambda text: isinstance(text, Comment)):
            comment.extract()
        
        # Get text from main content areas
        main_content = []
        
        # Try common content containers
        for tag in ['main', 'article', 'div[role="main"]', '.content', '.post', '.entry']:
            elements = soup.select(tag)
            if elements:
                for elem in elements:
                    text = elem.get_text(strip=True, separator=' ')
                    if len(text) > 200:  # Substantial content
                        main_content.append(text)
        
        # Fallback to body
        if not main_content:
            main_content = [soup.get_text(strip=True, separator=' ')]
        
        # Join and clean
        text = ' '.join(main_content)
        # Remove extra whitespace
        text = re.sub(r'\s+', ' ', text)
        
        return text[:10000]  # Limit to 10k chars
    
    def extract_concepts(self, text: str) -> List[str]:
        """Extract meaningful key phrases from page content."""
        if not text:
            return []
        stopwords = {
            'the', 'and', 'for', 'that', 'with', 'from', 'this', 'have', 'will', 'into', 'about', 'after',
            'your', 'their', 'were', 'been', 'which', 'when', 'while', 'where', 'within', 'through', 'among',
            'into', 'onto', 'each', 'such', 'than', 'them', 'they', 'them', 'those', 'these', 'ourselves',
            'could', 'would', 'should', 'might', 'there', 'here', 'also', 'very', 'much', 'make', 'made',
            'including', 'include', 'using', 'used', 'because', 'between', 'before', 'after', 'over', 'under',
            'again', 'still', 'being', 'only', 'even', 'most', 'more', 'less'
        }
        tokens = re.findall(r"[A-Za-z][A-Za-z\-']+", text)
        if not tokens:
            return []
        lower_tokens = [tok.lower() for tok in tokens]
        candidate_phrases: List[str] = []

        def is_content_word(word: str) -> bool:
            return len(word) >= 4 and word not in stopwords

        # Capture proper nouns / capitalized sequences
        for match in re.finditer(r'(?:[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,3})', text):
            phrase = match.group().strip()
            if len(phrase) >= 4:
                candidate_phrases.append(phrase)

        # High-value single words
        for word in lower_tokens:
            if is_content_word(word):
                candidate_phrases.append(word)

        # Bigrams and trigrams of content words
        for n in (2, 3):
            for i in range(len(lower_tokens) - n + 1):
                chunk = lower_tokens[i:i + n]
                if not any(is_content_word(w) for w in chunk):
                    continue
                if chunk[0] in stopwords and chunk[-1] in stopwords:
                    continue
                phrase = " ".join(chunk)
                candidate_phrases.append(phrase)

        counts = Counter()
        display_map: Dict[str, str] = {}
        for phrase in candidate_phrases:
            key = re.sub(r'\s+', ' ', phrase.lower()).strip()
            if not key:
                continue
            counts[key] += 1
            if key not in display_map:
                display_map[key] = phrase.strip()

        top_keys = [key for key, _ in counts.most_common(15)]
        concepts: List[str] = []
        for key in top_keys:
            phrase = display_map[key]
            cleaned = re.sub(r'\s+', ' ', phrase).strip()
            if not cleaned:
                continue
            if cleaned.islower():
                cleaned = cleaned.title()
            if len(cleaned) > 48:
                cleaned = cleaned[:45].rstrip() + "..."
            concepts.append(cleaned)
        return concepts
    
    def calculate_obscurity_score(self, url: str, title: str, content: str) -> float:
        """Calculate obscurity score (0-1, higher is more obscure)."""
        score = 0.5  # Base score
        
        # Check URL indicators
        url_lower = url.lower()
        for indicator in self.obscurity_indicators['high']:
            if indicator in url_lower:
                score += 0.2
        for indicator in self.obscurity_indicators['medium']:
            if indicator in url_lower:
                score += 0.1
        for indicator in self.obscurity_indicators['low']:
            if indicator in url_lower:
                score -= 0.1
        
        # Check title indicators
        title_lower = title.lower()
        for indicator in self.obscurity_indicators['high']:
            if indicator in title_lower:
                score += 0.15
        for indicator in self.obscurity_indicators['medium']:
            if indicator in title_lower:
                score += 0.05
        
        # Content depth (longer, more detailed content is often more obscure)
        if len(content) > 5000:
            score += 0.1
        elif len(content) > 2000:
            score += 0.05
        
        # Domain popularity (inverse)
        domain = urlparse(url).netloc
        if domain in ['wikipedia.org', 'github.com', 'stackoverflow.com']:
            score -= 0.2
        elif '.edu' in domain or '.gov' in domain:
            score += 0.15
        
        return min(max(score, 0), 1)
    
    def extract_interesting_bits(self, text: str) -> List[str]:
        """Extract interesting quotes and facts from text."""
        bits = []
        
        # Find quotes
        quotes = re.findall(r'"([^"]{30,100})"', text)
        bits.extend([f'"{q}"' for q in quotes[:3]])
        
        # Find surprising facts
        facts = re.findall(r'\b(\d+(?:\.\d+)?\s*(?:million|billion|trillion|percent|years|ago|old|wide|deep|tall|fast|slow))\b', text, re.IGNORECASE)
        bits.extend(facts[:2])
        
        # Find technical specifications
        specs = re.findall(r'\b(\d+(?:\.\d+)?\s*(?:gb|mb|kb|hz|ghz|thz|nm|μm|mm|cm|m|km|ms|μs|ns))\b', text, re.IGNORECASE)
        bits.extend(specs[:2])
        
        # Find dates with context
        dates = re.findall(r'\b(in\s+\d{4}|on\s+\w+\s+\d{1,2},\s+\d{4}|since\s+\d{4}|before\s+\d{4})\b', text, re.IGNORECASE)
        bits.extend(dates[:2])
        
        return bits[:5]  # Return top 5
    
    def scrape_url(self, url: str) -> ScrapedContent:
        """Scrape a URL and analyze its content."""
        # Check cache first
        cached = self.load_from_cache(url)
        if cached:
            print(f"    💾 Cache HIT: {url[:60]}...")
            return cached
        
        print(f"    🌐 Fetching: {url[:60]}...")
        
        # Fetch the page
        response = self.fetch_page(url)
        if not response:
            print(f"    ✗ Failed to fetch: {url[:60]}...")
            return ScrapedContent(
                url=url,
                title="",
                description="",
                content="",
                concepts=[],
                obscurity_score=0,
                accessibility_score=0,
                interesting_bits=[],
                error="Failed to fetch"
            )
        
        content_type = response.headers.get('Content-Type', '').lower()
        asset_type = self._infer_asset_type(url, content_type)
        if asset_type:
            print(f"    📄 Detected {asset_type}; using download metadata")
            return self._handle_non_html_asset(url, response, asset_type)

        # Parse HTML
        try:
            soup = BeautifulSoup(response.text, 'html.parser')
        except Exception as exc:
            print(f"    ⚠️  HTML parse failed; treating as generic asset: {exc}")
            return self._handle_non_html_asset(url, response, "download")
        
        # Extract metadata
        title = soup.find('title')
        title = title.get_text(strip=True) if title else url
        
        # Meta description
        desc = soup.find('meta', attrs={'name': 'description'})
        description = desc.get('content', '') if desc else ''
        
        # Extract main content
        content = self.extract_text(soup)
        
        # Analyze content
        concepts = self.extract_concepts(content)
        obscurity_score = self.calculate_obscurity_score(url, title, content)
        interesting_bits = self.extract_interesting_bits(content)
        
        # Calculate accessibility (simple check)
        accessibility_score = 1.0 if response.status_code == 200 else 0.0
        
        result = ScrapedContent(
            url=url,
            title=title,
            description=description,
            content=content,
            concepts=concepts,
            obscurity_score=obscurity_score,
            accessibility_score=accessibility_score,
            interesting_bits=interesting_bits
        )
        
        # Save to cache
        self.save_to_cache(result)
        print(f"    💾 Saved to cache")
        
        return result
    
    def validate_links(self, urls: List[str]) -> List[ScrapedContent]:
        """Validate and analyze a list of URLs."""
        results = []
        
        for url in urls:
            print(f"  Scraping: {url}")
            content = self.scrape_url(url)
            results.append(content)
            
            # Rate limiting
            time.sleep(0.5)
        
        return results
    
    def get_concepts_for_theme(self, theme: str, limit: int = 50) -> List[str]:
        """Get cached concepts relevant to a theme."""
        all_concepts = []
        
        # Load all cached content
        for cache_file in CACHE_DIR.glob("*.json"):
            try:
                with open(cache_file, 'r') as f:
                    data = json.load(f)
                content = ScrapedContent(**data)
                
                # Filter by obscurity and relevance
                if content.obscurity_score > 0.3 and content.concepts:
                    # Check if concepts relate to theme
                    theme_lower = theme.lower()
                    relevant_concepts = [
                        c for c in content.concepts
                        if any(word in c.lower() for word in theme_lower.split())
                    ]
                    all_concepts.extend(relevant_concepts)
            except:
                continue
        
        # Return unique concepts
        return list(set(all_concepts))[:limit]
