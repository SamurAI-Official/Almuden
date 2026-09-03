"""News / delisting / withdrawal-suspension monitor.

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
