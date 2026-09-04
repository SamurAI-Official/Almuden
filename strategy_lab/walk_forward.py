"""Walk-forward testing framework.

Walk-forward analysis is the gold standard for strategy validation:
rolling windows with train/test splits give realistic out-of-sample
performance estimates.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from config import Settings
from strategy_lab.backtester import Backtester, BacktestResult

log = logging.getLogger(__name__)


@dataclass
class WalkForwardFold:
    """Result from a single walk-forward fold."""
    fold_index: int
    train_start: str
    train_end: str
    test_start: str
    test_end: str
    train_metrics: Dict[str, float] = field(default_factory=dict)
    test_metrics: Dict[str, float] = field(default_factory=dict)
    degradation: float = 0.0  # performance drop from train to test


@dataclass
class WalkForwardResult:
    """Aggregated walk-forward analysis result."""
    strategy: str
    symbol: str
    venue: str
    folds: List[WalkForwardFold] = field(default_factory=list)
    aggregated_train_metrics: Dict[str, float] = field(default_factory=dict)
    aggregated_test_metrics: Dict[str, float] = field(default_factory=dict)
    walk_forward_efficiency: float = 0.0  # test return / train return
    is_robust: bool = False

    @property
    def avg_test_return(self) -> float:
        return self.aggregated_test_metrics.get("total_return_pct", 0.0)

    @property
    def avg_test_sharpe(self) -> float:
        return self.aggregated_test_metrics.get("sharpe_ratio", 0.0)

    @property

    def avg_test_drawdown(self) -> float:
        return self.aggregated_test_metrics.get("max_drawdown_pct", 0.0)

class WalkForwardAnalyzer:
    """Rolling walk-forward analysis for strategy validation."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._backtester = Backtester(settings)

    def analyze(self, strategy, venue: str, symbol: str, start: str, end: str,
            n_folds: int = 5, train_ratio: float = 0.7, timeframe: str = '1h',
            initial_balance: float = 10_000.0) -> WalkForwardResult:
        from strategy_lab.data import DataLoader
        loader = DataLoader(self._settings)
        df = loader.load(venue, symbol, start, end, timeframe)
        if df.empty or len(df) < n_folds * 10:
            log.warning('Insufficient data for walk-forward: %d rows', len(df))
            return WalkForwardResult(strategy=strategy.name, symbol=symbol, venue=venue)
        fold_size = len(df) // (n_folds + 1)
        folds = []
        for i in range(n_folds):
            ts = i * fold_size
            te = ts + int(fold_size * train_ratio)
            tse = min(te + fold_size, len(df))
            if tse > len(df):
                break
            train_df = df.iloc[ts:te]
            test_df = df.iloc[te:tse]
            train_result = self._run_fold(strategy, venue, symbol, train_df, initial_balance)
            test_result = self._run_fold(strategy, venue, symbol, test_df, initial_balance)
            train_return = train_result.metrics.get('total_return_pct', 0.0)
            test_return = test_result.metrics.get('total_return_pct', 0.0)
            degradation = train_return - test_return if train_return != 0 else 0.0
            folds.append(WalkForwardFold(
                fold_index=i,
                train_start=str(train_df.index[0])[:10],
                train_end=str(train_df.index[-1])[:10],
                test_start=str(test_df.index[0])[:10],
                test_end=str(test_df.index[-1])[:10],
                train_metrics=train_result.metrics,
                test_metrics=test_result.metrics,
                degradation=round(degradation, 4)))
        result = WalkForwardResult(strategy=strategy.name, symbol=symbol, venue=venue, folds=folds)
        self._aggregate(result)
        return result

    def _run_fold(self, strategy, venue, symbol, df, initial_balance):
        """Run a backtest on a specific data fold."""
        from trading.exchange import Book
        trades = []
        balance = initial_balance
        position = 0.0
        equity_curve = [balance]
        for i in range(len(df)):
            row = df.iloc[i]
            spread = row['close'] * 0.001
            mock_book = Book(venue, symbol,
                [(row['close'] - spread / 2, row['volume'] * 0.1)],
                [(row['close'] + spread / 2, row['volume'] * 0.1)])
            books = {(venue, symbol): mock_book}
            try:
                opportunities = strategy.scan(books)
            except Exception:
                continue
            for opp in opportunities:
                if not opp.is_viable:
                    continue
                buy_price = row['low'] * 1.001
                sell_price = row['high'] * 0.999
                size = min(opp.size, balance / buy_price) if opp.size > 0 else balance * 0.1 / buy_price
                if size <= 0 or balance <= 0:
                    continue
                fee_rate = 20.0 / 10_000.0
                cost = size * buy_price * (1 + fee_rate)
                proceeds = size * sell_price * (1 - fee_rate)
                if cost > balance:
                    continue
                pnl = proceeds - cost
                trades.append({
                    'timestamp': str(row.name), 'type': 'cross_venue',
                    'size': size, 'buy_price': buy_price, 'sell_price': sell_price,
                    'pnl': pnl, 'fee': cost * fee_rate + proceeds * fee_rate,
                    'balance_after': balance - cost + proceeds, 'position_after': position,
                    'edge_bps': opp.expected_edge_bps})
                balance = balance - cost + proceeds
            equity_curve.append(balance + position * row['close'])
        result = BacktestResult(strategy=strategy.name, venue=venue, symbol=symbol,
            start_date='', end_date='', trades=trades, equity_curve=equity_curve)
        result.metrics = self._backtester._calculate_metrics(result)
        return result

    def _aggregate(self, result):
        """Aggregate metrics across folds and compute robustness."""
        if not result.folds:
            return
        train_metrics = {}
        test_metrics = {}
        for fold in result.folds:
            for key, value in fold.train_metrics.items():
                if isinstance(value, (int, float)):
                    train_metrics.setdefault(key, []).append(value)
            for key, value in fold.test_metrics.items():
                if isinstance(value, (int, float)):
                    test_metrics.setdefault(key, []).append(value)
        result.aggregated_train_metrics = {
            k: round(sum(v) / len(v), 4) for k, v in train_metrics.items()
        }
        result.aggregated_test_metrics = {
            k: round(sum(v) / len(v), 4) for k, v in test_metrics.items()
        }
        train_return = result.aggregated_train_metrics.get('total_return_pct', 0.0)
        test_return = result.aggregated_test_metrics.get('total_return_pct', 0.0)
        if train_return > 0:
            result.walk_forward_efficiency = round(test_return / train_return, 4)
        else:
            result.walk_forward_efficiency = 0.0
        result.is_robust = (
            result.walk_forward_efficiency > 0.5
            and result.aggregated_test_metrics.get('sharpe_ratio', 0) > 0.5
            and result.aggregated_test_metrics.get('max_drawdown_pct', 100) < 15.0
        )
