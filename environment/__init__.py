"""Environment layer — unified sensory surface for market data, news, and exchange health.

The Environment facade polls all three pillars and returns a single EnvironmentState
that the agent system and strategy lab can consume.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from config import Settings
from trading.exchange import Book
from environment.market import MarketSnapshot, MarketFeed
from environment.news import NewsFeed, NewsItem
from environment.exchanges import ExchangeHealth, ExchangeHealthMonitor
from environment.regime import Regime, RegimeDetector
from environment.sentiment import SentimentScore, SentimentScorer

log = logging.getLogger(__name__)


@dataclass
class EnvironmentState:
    """Unified snapshot of the trading environment."""
    market: MarketSnapshot
    news: List[NewsItem] = field(default_factory=list)
    exchange_health: Dict[str, ExchangeHealth] = field(default_factory=dict)
    regime: Regime = Regime.UNKNOWN
    sentiment: Dict[str, SentimentScore] = field(default_factory=dict)
    timestamp: float = 0.0

    @property
    def healthy_venues(self) -> List[str]:
        """Venues that are currently healthy."""
        return [
            venue for venue, health in self.exchange_health.items()
            if health.is_healthy
        ]

    @property
    def critical_news(self) -> List[NewsItem]:
        """News items with critical severity."""
        return [n for n in self.news if n.severity == "critical"]

    @property
    def has_critical_news(self) -> bool:
        """True if any critical news is present."""
        return len(self.critical_news) > 0

    def summary(self) -> Dict:
        """Return a JSON-serializable summary."""
        return {
            "regime": self.regime.value,
            "healthy_venues": self.healthy_venues,
            "news_count": len(self.news),
            "critical_news": len(self.critical_news),
            "sentiment": {
                asset: score.value for asset, score in self.sentiment.items()
            },
            "books": len(self.market.books),
        }


class Environment:
    """Facade that polls all environment pillars."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._market = MarketFeed(settings)
        self._news = NewsFeed(settings)
        self._exchanges = ExchangeHealthMonitor(settings)
        self._regime = RegimeDetector(settings)
        self._sentiment = SentimentScorer()
        self._last_state: Optional[EnvironmentState] = None

    async def poll(self) -> EnvironmentState:
        """Poll all pillars and return a unified state."""
        import time

        # Poll market data (books)
        books = await self._market.poll_books()

        # Build market snapshot
        market_snapshot = self._market.snapshot(books)

        # Detect regime from market data
        regime = self._regime.detect(books)

        # Poll news
        news_items = await self._news.poll()

        # Score sentiment
        sentiment = self._sentiment.score_many(news_items)

        # Check exchange health
        exchange_health = await self._exchanges.check(books)

        state = EnvironmentState(
            market=market_snapshot,
            news=news_items,
            exchange_health=exchange_health,
            regime=regime,
            sentiment=sentiment,
            timestamp=time.time(),
        )

        self._last_state = state
        return state

    @property
    def last_state(self) -> Optional[EnvironmentState]:
        return self._last_state

    async def close(self) -> None:
        await self._market.close()
        await self._news.close()
        await self._exchanges.close()