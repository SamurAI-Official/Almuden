"""Event-driven backtester.

Feeds historical bars through strategies and simulates fills.
Reuses the same Strategy interface as the live engine.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import pandas as pd

from config import Settings
from strategy_lab.base import Opportunity
from strategy_lab.data import DataLoader

log = logging.getLogger(__name__)


@dataclass
class BacktestResult:
    """Results from a backtest run."""
    strategy: str
    venue: str
    symbol: str
    start_date: str
    end_date: str
    trades: List[Dict] = field(default_factory=list)
    equity_curve: List[float] = field(default_factory=list)
    metrics: Dict[str, float] = field(default_factory=dict)

    @property
    def total_trades(self) -> int:
        return len(self.trades)

    @property
    def total_pnl(self) -> float:
        return sum(t.get("pnl", 0) for t in self.trades)


class Backtester:
    """Event-driven backtester for strategies."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._data_loader = DataLoader(settings)

    def run(
        self,
        strategy,
        venue: str,
        symbol: str,
        start: str,
        end: str,
        timeframe: str = "1h",
        initial_balance: float = 10_000.0,
    ) -> BacktestResult:
        """Run a backtest for a strategy.

        Args:
            strategy: Strategy instance to test
            venue: Exchange to simulate on
            symbol: Trading pair
            start: Start date (YYYY-MM-DD)
            end: End date (YYYY-MM-DD)
            timeframe: Candle timeframe
            initial_balance: Starting balance in quote currency

        Returns:
            BacktestResult with trades and equity curve
        """
        # Load data
        df = self._data_loader.load(venue, symbol, start, end, timeframe)
        if df.empty:
            log.warning("No data for %s %s %s to %s", venue, symbol, start, end)
            return BacktestResult(
                strategy=strategy.name,
                venue=venue,
                symbol=symbol,
                start_date=start,
                end_date=end,
            )

        # Run simulation
        trades = []
        balance = initial_balance
        equity_curve = [balance]
        position = 0.0  # Base asset held

        for i in range(len(df)):
            row = df.iloc[i]
            timestamp = df.index[i]

            # Build a mock book from OHLCV data
            mock_book = self._build_mock_book(venue, symbol, row)

            # Scan for opportunities
            books = {(venue, symbol): mock_book}
            try:
                opportunities = strategy.scan(books)
            except Exception as exc:
                log.debug("Strategy scan error: %s", exc)
                continue

            # Simulate fills for viable opportunities
            for opp in opportunities:
                if not opp.is_viable:
                    continue
                trade = self._simulate_fill(opp, row, balance, position)
                if trade:
                    trades.append(trade)
                    balance = trade["balance_after"]
                    position = trade["position_after"]

            equity_curve.append(balance + position * row["close"])

        # Calculate metrics
        result = BacktestResult(
            strategy=strategy.name,
            venue=venue,
            symbol=symbol,
            start_date=start,
            end_date=end,
            trades=trades,
            equity_curve=equity_curve,
        )
        result.metrics = self._calculate_metrics(result)

        return result

    def _build_mock_book(self, venue: str, symbol: str, row: pd.Series):
        """Build a mock Book from OHLCV data."""
        from trading.exchange import Book

        # Create synthetic bids/asks around close price
        spread = row["close"] * 0.001  # 0.1% spread
        bid_price = row["close"] - spread / 2
        ask_price = row["close"] + spread / 2

        bids = [(bid_price, row["volume"] * 0.1)]
        asks = [(ask_price, row["volume"] * 0.1)]

        return Book(venue, symbol, bids, asks)

    def _simulate_fill(
        self,
        opp: Opportunity,
        row: pd.Series,
        balance: float,
        position: float,
    ) -> Optional[Dict]:
        """Simulate a fill for an opportunity."""
        fee_bps = 20.0  # Simplified fee
        fee_rate = fee_bps / 10_000.0

        if opp.strategy == "cross_venue":
            buy_price = row["low"] * 1.001  # Slight slippage
            sell_price = row["high"] * 0.999

            size = min(opp.size, balance / buy_price)
            if size <= 0:
                return None

            cost = size * buy_price * (1 + fee_rate)
            proceeds = size * sell_price * (1 - fee_rate)

            if cost > balance:
                return None

            pnl = proceeds - cost
            new_balance = balance - cost + proceeds
            new_position = position  # Cross-venue: position neutral

            return {
                "timestamp": row.name,
                "type": "cross_venue",
                "size": size,
                "buy_price": buy_price,
                "sell_price": sell_price,
                "pnl": pnl,
                "fee": cost * fee_rate + proceeds * fee_rate,
                "balance_after": new_balance,
                "position_after": new_position,
                "edge_bps": opp.expected_edge_bps,
            }

        return None

    def _calculate_metrics(self, result: BacktestResult) -> Dict[str, float]:
        """Calculate performance metrics."""
        from strategy_lab.performance import calculate_metrics

        return calculate_metrics(result.equity_curve, result.trades)