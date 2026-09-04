"""Research scorecard — comprehensive strategy evaluation metrics."""
from __future__ import annotations
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class ResearchScorecard:
    """Multi-dimensional strategy quality assessment."""
    total_trades: int = 0
    total_return_pct: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    max_drawdown_pct: float = 0.0
    calmar_ratio: float = 0.0
    avg_trade_pnl: float = 0.0
    expectancy: float = 0.0
    train_return_pct: float = 0.0
    test_return_pct: float = 0.0
    fold_count: int = 0
    fold_returns: List[float] = field(default_factory=list)
    expectancy_score: float = 0.0
    profit_factor_score: float = 0.0
    sharpe_score: float = 0.0
    sortino_score: float = 0.0
    drawdown_score: float = 0.0
    calmar_score: float = 0.0
    win_rate_score: float = 0.0
    sample_size_score: float = 0.0
    degradation_score: float = 0.0
    consistency_score: float = 0.0
    robustness_score: float = 0.0

    def compute_scores(self) -> None:
        """Compute all normalized scores from raw metrics."""
        self.expectancy_score = _saturate(self.expectancy / 0.01)
        self.profit_factor_score = _saturate(self.profit_factor / 2.0)
        self.sharpe_score = _saturate(self.sharpe_ratio / 2.0)
        self.sortino_score = _saturate(self.sortino_ratio / 3.0)
        self.drawdown_score = _saturate(1.0 - self.max_drawdown_pct / 20.0)
        self.calmar_score = _saturate(self.calmar_ratio / 3.0)
        self.win_rate_score = _saturate(self.win_rate / 60.0)
        self.sample_size_score = _saturate(self.total_trades / 200.0)
        if self.train_return_pct > 0:
            ratio = self.test_return_pct / self.train_return_pct
            self.degradation_score = _saturate(ratio)
        else:
            self.degradation_score = 0.0
        if len(self.fold_returns) > 1:
            mean_ret = sum(self.fold_returns) / len(self.fold_returns)
            if mean_ret != 0:
                variance = sum((r - mean_ret) ** 2 for r in self.fold_returns) / len(self.fold_returns)
                cv = math.sqrt(variance) / abs(mean_ret)
                self.consistency_score = _saturate(1.0 - cv)
            else:
                self.consistency_score = 0.0
        else:
            self.consistency_score = 0.5
        weights = {"sharpe": 0.20, "sortino": 0.10, "drawdown": 0.15, "calmar": 0.10,
            "profit_factor": 0.10, "sample_size": 0.10, "degradation": 0.15, "consistency": 0.10}
        self.robustness_score = (
            weights["sharpe"] * self.sharpe_score + weights["sortino"] * self.sortino_score
            + weights["drawdown"] * self.drawdown_score + weights["calmar"] * self.calmar_score
            + weights["profit_factor"] * self.profit_factor_score + weights["sample_size"] * self.sample_size_score
            + weights["degradation"] * self.degradation_score + weights["consistency"] * self.consistency_score)

    def to_dict(self) -> Dict[str, float]:
        return {"total_trades": self.total_trades, "total_return_pct": round(self.total_return_pct, 4),
            "win_rate": round(self.win_rate, 2), "profit_factor": round(self.profit_factor, 4),
            "sharpe_ratio": round(self.sharpe_ratio, 4), "sortino_ratio": round(self.sortino_ratio, 4),
            "max_drawdown_pct": round(self.max_drawdown_pct, 4), "calmar_ratio": round(self.calmar_ratio, 4),
            "robustness_score": round(self.robustness_score, 4)}


def _saturate(value: float, floor: float = 0.0, ceiling: float = 1.0) -> float:
    return max(floor, min(ceiling, value))

def compute_metrics_from_equity(
    equity_curve: List[float],
    trades: List[Dict],
    train_equity: Optional[List[float]] = None,
    test_equity: Optional[List[float]] = None,
    fold_returns: Optional[List[float]] = None,
) -> ResearchScorecard:
    """Compute a full research scorecard from equity curve and trades."""
    if not equity_curve or len(equity_curve) < 2:
        return ResearchScorecard()
    card = ResearchScorecard()
    card.total_trades = len(trades)
    card.fold_returns = fold_returns or []
    start_equity = equity_curve[0]
    end_equity = equity_curve[-1]
    card.total_return_pct = ((end_equity / start_equity) - 1.0) * 100 if start_equity > 0 else 0.0
    returns = []
    for i in range(1, len(equity_curve)):
        if equity_curve[i - 1] > 0:
            returns.append((equity_curve[i] - equity_curve[i - 1]) / equity_curve[i - 1])
    if not returns:
        card.compute_scores()
        return card
    mean_return = sum(returns) / len(returns)
    variance = sum((r - mean_return) ** 2 for r in returns) / len(returns)
    volatility = math.sqrt(variance) * math.sqrt(24 * 365)
    if volatility > 0:
        card.sharpe_ratio = (mean_return * 24 * 365) / (volatility / math.sqrt(24 * 365))
    downside_returns = [r for r in returns if r < 0]
    if downside_returns:
        downside_var = sum(r ** 2 for r in downside_returns) / len(downside_returns)
        downside_dev = math.sqrt(downside_var) * math.sqrt(24 * 365)
        if downside_dev > 0:
            card.sortino_ratio = (mean_return * 24 * 365) / downside_dev
    peak = equity_curve[0]
    max_dd = 0.0
    for eq in equity_curve:
        if eq > peak:
            peak = eq
        dd = (peak - eq) / peak if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd
    card.max_drawdown_pct = max_dd * 100
    if max_dd > 0:
        annual_return = mean_return * 24 * 365
        card.calmar_ratio = annual_return / max_dd
    if trades:
        wins = [t for t in trades if t.get('pnl', 0) > 0]
        losses = [t for t in trades if t.get('pnl', 0) < 0]
        card.win_rate = len(wins) / len(trades) * 100
        gross_profit = sum(t.get('pnl', 0) for t in wins)
        gross_loss = abs(sum(t.get('pnl', 0) for t in losses))
        if gross_loss > 0:
            card.profit_factor = gross_profit / gross_loss
        if trades:
            card.avg_trade_pnl = sum(t.get('pnl', 0) for t in trades) / len(trades)
            if start_equity > 0:
                card.expectancy = card.avg_trade_pnl / start_equity
    if train_equity and test_equity and len(train_equity) > 1 and len(test_equity) > 1:
        train_ret = ((train_equity[-1] / train_equity[0]) - 1) * 100 if train_equity[0] > 0 else 0
        test_ret = ((test_equity[-1] / test_equity[0]) - 1) * 100 if test_equity[0] > 0 else 0
        card.train_return_pct = train_ret
        card.test_return_pct = test_ret
    card.compute_scores()
    return card
