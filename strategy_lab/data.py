"""Data loader — fetches OHLCV data from CCXT or loads from CSV.

Caches data to disk as Parquet for fast reloading.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Dict, List, Optional

import pandas as pd

from config import Settings

log = logging.getLogger(__name__)


@dataclass
class Bar:
    """A single OHLCV bar."""
    timestamp: int  # Unix timestamp in ms
    open: float
    high: float
    low: float
    close: float
    volume: float


class DataLoader:
    """Loads historical OHLCV data."""

    def __init__(self, settings: Settings, cache_dir: str = ".cache") -> None:
        self._settings = settings
        self._cache_dir = cache_dir
        os.makedirs(cache_dir, exist_ok=True)

    def load(
        self,
        venue: str,
        symbol: str,
        start: str,
        end: str,
        timeframe: str = "1h",
    ) -> pd.DataFrame:
        """Load OHLCV data for a symbol.

        Args:
            venue: Exchange name (e.g., "kucoin")
            symbol: Trading pair (e.g., "ERG/USDT")
            start: Start date (YYYY-MM-DD)
            end: End date (YYYY-MM-DD)
            timeframe: Candle timeframe (e.g., "1h", "1d")

        Returns:
            DataFrame with columns: timestamp, open, high, low, close, volume
        """
        cache_key = f"{venue}_{symbol}_{timeframe}_{start}_{end}".replace("/", "_")
        cache_path = os.path.join(self._cache_dir, f"{cache_key}.parquet")

        # Check cache first
        if os.path.exists(cache_path):
            log.info("Loading cached data: %s", cache_path)
            return pd.read_parquet(cache_path)

        # Fetch from exchange
        df = self._fetch_from_exchange(venue, symbol, start, end, timeframe)

        # Cache to disk
        if df is not None and not df.empty:
            df.to_parquet(cache_path)
            log.info("Cached data to: %s", cache_path)

        return df if df is not None else pd.DataFrame()

    def _fetch_from_exchange(
        self,
        venue: str,
        symbol: str,
        start: str,
        end: str,
        timeframe: str,
    ) -> Optional[pd.DataFrame]:
        """Fetch OHLCV data from an exchange via CCXT."""
        try:
            import ccxt.async_support as ccxt
            from trading.exchange import VENUE_MAP

            ccxt_id = VENUE_MAP.get(venue)
            if ccxt_id is None or not hasattr(ccxt, ccxt_id):
                log.warning("Unsupported venue: %s", venue)
                return None

            exchange = getattr(ccxt, ccxt_id)({
                "enableRateLimit": True,
                "options": {"defaultType": "spot"},
            })

            # Convert dates to timestamps
            start_ts = int(pd.Timestamp(start).timestamp() * 1000)
            end_ts = int(pd.Timestamp(end).timestamp() * 1000)

            all_bars = []
            current_ts = start_ts

            # Paginate through data (CCXT has limits per request)
            while current_ts < end_ts:
                try:
                    bars = exchange.fetch_ohlcv(
                        symbol, timeframe, since=current_ts, limit=1000
                    )
                    if not bars:
                        break
                    all_bars.extend(bars)
                    # Move to last bar timestamp + 1
                    current_ts = bars[-1][0] + 1
                    # Check if we've reached the end
                    if bars[-1][0] >= end_ts:
                        break
                except Exception as exc:
                    log.warning("Fetch error: %s", exc)
                    break

            exchange.close()

            if not all_bars:
                return None

            df = pd.DataFrame(
                all_bars,
                columns=["timestamp", "open", "high", "low", "close", "volume"],
            )
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
            df = df[(df["timestamp"] >= start) & (df["timestamp"] <= end)]
            df.set_index("timestamp", inplace=True)

            return df

        except Exception as exc:
            log.error("Failed to fetch data: %s", exc)
            return None

    def load_csv(self, filepath: str) -> pd.DataFrame:
        """Load OHLCV data from a CSV file."""
        try:
            df = pd.read_csv(filepath, parse_dates=["timestamp"])
            df.set_index("timestamp", inplace=True)
            return df
        except Exception as exc:
            log.error("Failed to load CSV: %s", exc)
            return pd.DataFrame()