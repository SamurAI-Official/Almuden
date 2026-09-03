"""Sentiment scoring — lexicon-based analysis of news and headlines.

Scores each asset on a scale from -1.0 (very bearish) to +1.0 (very bullish).
No LLM required — uses a simple weighted keyword approach.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Dict, List, Optional

from environment.news import NewsItem

log = logging.getLogger(__name__)


@dataclass
class SentimentScore:
    """Sentiment score for a single asset."""
    asset: str
    value: float  # -1.0 to +1.0
    confidence: float  # 0.0 to 1.0
    sources: int = 0

    def __str__(self) -> str:
        direction = "bullish" if self.value > 0.1 else "bearish" if self.value < -0.1 else "neutral"
        return f"{self.asset}: {direction} ({self.value:+.2f}, conf={self.confidence:.2f})"


# Lexicon of terms with sentiment weights
# Positive terms indicate bullish sentiment
POSITIVE_TERMS = {
    "upgrade": 0.5,
    "partnership": 0.4,
    "adoption": 0.6,
    "growth": 0.4,
    "surge": 0.5,
    "rally": 0.5,
    "breakout": 0.4,
    "support": 0.3,
    "buy": 0.3,
    "accumulation": 0.4,
    "staking": 0.3,
    "reward": 0.3,
    "airdrop": 0.4,
    "listing": 0.5,
    "listed": 0.5,
    "relisting": 0.6,
    "relisted": 0.6,
    "mainnet": 0.4,
    "launch": 0.4,
    "innovation": 0.3,
    "development": 0.2,
    "grant": 0.3,
    "funding": 0.4,
    "integration": 0.4,
    "collaboration": 0.3,
}

# Negative terms indicate bearish sentiment
NEGATIVE_TERMS = {
    "delist": -0.8,
    "delisting": -0.8,
    "delisted": -0.8,
    "suspend": -0.6,
    "suspension": -0.6,
    "halt": -0.5,
    "terminate": -0.7,
    "termination": -0.7,
    "close": -0.4,
    "closing": -0.4,
    "remove": -0.5,
    "removal": -0.5,
    "ban": -0.8,
    "banned": -0.8,
    "regulation": -0.3,
    "regulatory": -0.3,
    "sec": -0.4,
    "investigation": -0.5,
    "lawsuit": -0.6,
    "hack": -0.8,
    "exploit": -0.7,
    "vulnerability": -0.5,
    "crash": -0.7,
    "dump": -0.6,
    "sell": -0.2,
    "selling": -0.3,
    "fear": -0.4,
    "risk": -0.2,
    "warning": -0.3,
    "concern": -0.2,
    "frozen": -0.5,
    "restrict": -0.4,
    "discontinue": -0.6,
    "end": -0.3,
    "withdraw": -0.3,
    "withdrawal": -0.3,
    "disable": -0.4,
}

# Compile patterns for efficiency
_POSITIVE_PATTERNS = {
    term: re.compile(r"\b" + re.escape(term) + r"\b", re.IGNORECASE)
    for term in POSITIVE_TERMS
}
_NEGATIVE_PATTERNS = {
    term: re.compile(r"\b" + re.escape(term) + r"\b", re.IGNORECASE)
    for term in NEGATIVE_TERMS
}


class SentimentScorer:
    """Scores sentiment from news items using a weighted lexicon."""

    def score_text(self, text: str) -> float:
        """Score a single text string. Returns -1.0 to +1.0."""
        if not text:
            return 0.0

        score = 0.0
        matches = 0

        for term, pattern in _POSITIVE_PATTERNS.items():
            if pattern.search(text):
                score += POSITIVE_TERMS[term]
                matches += 1

        for term, pattern in _NEGATIVE_PATTERNS.items():
            if pattern.search(text):
                score += NEGATIVE_TERMS[term]
                matches += 1

        if matches == 0:
            return 0.0

        # Normalize to [-1, 1]
        return max(-1.0, min(1.0, score / max(matches, 1)))

    def score_news_item(self, item: NewsItem) -> float:
        """Score a single news item."""
        text = f"{item.title} {item.body}"
        base_score = self.score_text(text)

        # Amplify score for critical items
        if item.severity == "critical":
            base_score *= 1.5
            base_score = max(-1.0, min(1.0, base_score))

        return base_score

    def score_many(self, items: List[NewsItem]) -> Dict[str, SentimentScore]:
        """Score sentiment per asset from a list of news items."""
        asset_scores: Dict[str, List[float]] = {}

        for item in items:
            score = self.score_news_item(item)
            for asset in item.assets:
                if asset not in asset_scores:
                    asset_scores[asset] = []
                asset_scores[asset].append(score)

        results = {}
        for asset, scores in asset_scores.items():
            avg_score = sum(scores) / len(scores)
            # Confidence based on number of sources
            confidence = min(1.0, len(scores) / 5.0)
            results[asset] = SentimentScore(
                asset=asset,
                value=round(avg_score, 4),
                confidence=round(confidence, 4),
                sources=len(scores),
            )

        return results