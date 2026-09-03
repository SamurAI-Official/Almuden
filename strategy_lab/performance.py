"""Performance metrics — Sharpe, Sortino, drawdown, win rate, etc."""
from __future__ import annotations

import logging
import math
from typing import Dict, List

log = logging.getLogger(__name__)


def calculate_metrics(equity_curve: List[float], trades: List[Dict]) -> Dict[str, float]:
    """Calculate performance metrics from equity curve and trades."""
    if not equity_curve or len(equity_curve) < 2:
        return {}

    metrics = {}

    # Total return
    start_equity = equity_curve[0]
    end_equity = equity_curve[-1]
    metrics["total_return_pct"] = round(
        ((end_equity / start_equity) - 1.0) * 100, 4
    ) if start_equity > 0 else 0.0

    # Returns series
    returns = []
    for i in range(1, len(equity_curve)):
        if equity_curve[i - 1] > 0:
            returns.append((equity_curve[i] - equity_curve[i - 1]) / equity_curve[i - 1])

    if not returns:
        return metrics

    # Mean return
    mean_return = sum(returns) / len(returns)

    # Volatility (annualized, assuming hourly data)
    variance = sum((r - mean_return) ** 2 for r in returns) / len(returns)
    volatility = math.sqrt(variance) * math.sqrt(24 * 365)  # Annualized
    metrics["volatility_annualized"] = round(volatility * 100, 4)

    # Sharpe ratio (assuming 0% risk-free rate)
    if volatility > 0:
        sharpe = (mean_return * 24 * 365) / (volatility / math.sqrt(24 * 365))
        metrics["sharpe_ratio"] = round(sharpe, 4)
    else:
        metrics["sharpe_ratio"] = 0.0

    # Sortino ratio (downside deviation only)
    downside_returns = [r for r in returns if r < 0]
    if downside_returns:
        downside_var = sum(r ** 2 for r in downside_returns) / len(downside_returns)
        downside_dev = math.sqrt(downside_var) * math.sqrt(24 * 365)
        if downside_dev > 0:
            sortino = (mean_return * 24 * 365) / downside_dev
            metrics["sortino_ratio"] = round(sortino, 4)
        else:
            metrics["sortino_ratio"] = 0.0
    else:
        metrics["sortino_ratio"] = float("inf")

    # Max drawdown
    peak = equity_curve[0]
    max_dd = 0.0
    for equity in equity_curve:
        if equity > peak:
            peak = equity
        dd = (peak - equity) / peak if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd
    metrics["max_drawdown_pct"] = round(max_dd * 100, 4)

    # Win rate
    if trades:
        wins = sum(1 for t in trades if t.get("pnl", 0) > 0)
        metrics["win_rate"] = round(wins / len(trades) * 100, 2)
        metrics["total_trades"] = len(trades)

        # Profit factor
        gross_profit = sum(t.get("pnl", 0) for t in trades if t.get("pnl", 0) > 0)
        gross_loss = abs(sum(t.get("pnl", 0) for t in trades if t.get("pnl", 0) < 0))
        if gross_loss > 0:
            metrics["profit_factor"] = round(gross_profit / gross_loss, 4)
        else:
            metrics["profit_factor"] = float("inf")

        # Average win/loss
        wins_list = [t.get("pnl", 0) for t in trades if t.get("pnl", 0) > 0]
        losses_list = [t.get("pnl", 0) for t in trades if t.get("pnl", 0) < 0]
        metrics["avg_win"] = round(sum(wins_list) / len(wins_list), 4) if wins_list else 0.0
        metrics["avg_loss"] = round(sum(losses_list) / len(losses_list), 4) if losses_list else 0.0

    # Calmar ratio (return / max drawdown)
    if max_dd > 0:
        annual_return = mean_return * 24 * 365
        metrics["calmar_ratio"] = round(annual_return / max_dd, 4)
    else:
        metrics["calmar_ratio"] = 0.0

    return metrics


def format_metrics(metrics: Dict[str, float]) -> str:
    """Format metrics for display."""
    lines = []
    for key, value in sorted(metrics.items()):
        if isinstance(value, float):
            if value == float("inf"):
                lines.append(f"  {key}: inf")
            else:
                lines.append(f"  {key}: {value:.4f}")
        else:
            lines.append(f"  {key}: {value}")
    return "\n".join(lines)