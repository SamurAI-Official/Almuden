"""News feed — fetches and monitors exchange announcements.

Polls exchange announcement feeds and flags items mentioning ERG or XMR
alongside risk keywords (delist, suspend, halt, close, terminate).

XMR is especially important: Binance, OKX, and others have delisted it.
A delisting or withdrawal suspension is a tail risk that must surface
immediately.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import List, Optional

from config import Settings

log = logging.getLogger(__name__)

# Risk keywords (case-insensitive)
RISK_KEYWORDS = [
    "delist", "delisting", "suspend", "suspension", "halt", "terminate",
    "termination", "close", "closing", "remove", "removal", "end", "discontinue",
    "withdraw", "withdrawal", "disable", "frozen", "restrict",
]

# Assets we monitor
ASSETS = ["ERG", "XMR", "MONERO", "ERGO"]

# Compiled patterns
_ASSET_PATTERNS = [re.compile(r"\b" + re.escape(a) + r"\b", re.IGNORECASE) for a in ASSETS]
_RISK_PATTERN = re.compile("|".join(RISK_KEYWORDS), re.IGNORECASE)


@dataclass
class NewsItem:
    """A single news item with risk assessment."""
    source: str
    title: str
    url: str = ""
    body: str = ""
    assets: List[str] = field(default_factory=list)
    risk_terms: List[str] = field(default_factory=list)
    severity: str = "info"  # info | warning | critical

    def __str__(self) -> str:
        return f"[{self.severity.upper()}] {self.source}: {self.title}"


class NewsMonitor:
    """Scan text for asset + risk-keyword matches."""

    def __init__(
        self,
        assets: Optional[List[str]] = None,
        risk_keywords: Optional[List[str]] = None,
    ) -> None:
        self._assets = assets or ASSETS
        self._risk_keywords = risk_keywords or RISK_KEYWORDS
        self._asset_patterns = [
            re.compile(r"\b" + re.escape(a) + r"\b", re.IGNORECASE)
            for a in self._assets
        ]
        self._risk_pattern = re.compile(
            "|".join(re.escape(k) for k in self._risk_keywords), re.IGNORECASE
        )

    def scan(self, source: str, title: str, body: str = "", url: str = "") -> Optional[NewsItem]:
        """Return a NewsItem if *title*+*body* mention an asset and a risk term."""
        text = f"{title} {body}"
        assets = [p.pattern.strip(r"\b") for p in self._asset_patterns if p.search(text)]
        if not assets:
            return None
        risk_terms = list(set(self._risk_pattern.findall(text)))
        if not risk_terms:
            return None
        severity = "critical" if any(
            k in " ".join(risk_terms).lower()
            for k in ["delist", "delisting", "terminate", "suspend", "halt"]
        ) else "warning"
        return NewsItem(
            source=source,
            title=title,
            url=url,
            body=body[:500],
            assets=assets,
            risk_terms=risk_terms,
            severity=severity,
        )

    def scan_many(self, items: List[dict]) -> List[NewsItem]:
        """Scan a list of {source, title, body?, url?} dicts."""
        results: List[NewsItem] = []
        for item in items:
            hit = self.scan(
                source=item.get("source", "unknown"),
                title=item.get("title", ""),
                body=item.get("body", ""),
                url=item.get("url", ""),
            )
            if hit:
                results.append(hit)
        return results


class NewsFeed:
    """Feed that polls exchange announcement APIs and monitors for risk events."""

    ANNOUNCEMENT_URLS = {
        "kucoin": "https://www.kucoin.com/_api/cms/articles?category=announcement&lang=en_US&page=1&pageSize=10",
        "gateio": "https://www.gate.io/apiw/v1/article/list?type=1&page=1&limit=10",
        "mexc": "https://www.mexc.com/api/platform/announcement/list?page=1&rows=10",
    }

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._monitor = NewsMonitor()
        self._seen_urls: set = set()

    async def poll(self) -> List[NewsItem]:
        """Poll all configured news sources and return new risk items."""
        items: List[NewsItem] = []
        for venue in self._settings.venues:
            if venue in self.ANNOUNCEMENT_URLS:
                try:
                    venue_items = await self._poll_venue(venue)
                    items.extend(venue_items)
                except Exception as exc:
                    log.debug("Failed to poll %s news: %s", venue, exc)
        return items

    async def _poll_venue(self, venue: str) -> List[NewsItem]:
        """Poll a single venue's announcement feed."""
        import aiohttp

        url = self.ANNOUNCEMENT_URLS.get(venue)
        if not url:
            return []

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status != 200:
                        return []
                    data = await resp.json()
        except Exception as exc:
            log.debug("HTTP error for %s: %s", venue, exc)
            return []

        articles = self._parse_response(venue, data)
        return self._monitor.scan_many(articles)

    def _parse_response(self, venue: str, data: dict) -> List[dict]:
        """Parse exchange-specific response format into uniform articles."""
        articles = []

        if venue == "kucoin":
            items = data.get("data", {}).get("items", [])
            for item in items:
                articles.append({
                    "source": "kucoin",
                    "title": item.get("title", ""),
                    "body": item.get("summary", ""),
                    "url": f"https://www.kucoin.com/news/detail/{item.get('id', '')}",
                })

        elif venue == "gateio":
            items = data.get("data", [])
            for item in items:
                articles.append({
                    "source": "gateio",
                    "title": item.get("title", ""),
                    "body": item.get("content", "")[:500],
                    "url": item.get("url", ""),
                })

        elif venue == "mexc":
            items = data.get("data", {}).get("list", [])
            for item in items:
                articles.append({
                    "source": "mexc",
                    "title": item.get("title", ""),
                    "body": item.get("content", "")[:500],
                    "url": f"https://www.mexc.com/support/articles/{item.get('id', '')}",
                })

        # Filter out already-seen URLs
        new_articles = []
        for article in articles:
            url = article.get("url", "")
            if url and url not in self._seen_urls:
                self._seen_urls.add(url)
                new_articles.append(article)

        return new_articles

    async def close(self) -> None:
        """No-op for now (aiohttp sessions are per-request)."""
        pass