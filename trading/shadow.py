"""Shadow broker - hypothetical execution against real market data.

Records what WOULD have happened if a strategy had traded, without
spending any capital. Enables comparison of predicted vs paper vs real.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from config import Settings
from trading.core import Fill, OrderIntent

log = logging.getLogger(__name__)


@dataclass
class ShadowTrade:
    execution_id: str
    strategy: str
    venue: str
    symbol: str
    side: str
    size: float
    price: float
    fee: float
    cost: float
    proceeds: float
    timestamp: float
    hyp_pnl: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ShadowSnapshot:
    strategy: str
    total_trades: int
    total_pnl: float
    total_fees: float
    win_rate: float
    avg_trade_pnl: float
    max_drawdown_pct: float
    sharpe: float
    equity_curve: List[float]


class ShadowBroker:
    FEE_BPS = 10.0

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._trades: List[ShadowTrade] = []
        self._equity_curve: List[float] = [0.0]
        self._peak_pnl: float = 0.0

    @property
    def trades(self) -> List[ShadowTrade]:
        return list(self._trades)

    @property
    def equity_curve(self) -> List[float]:
        return list(self._equity_curve)

    async def execute(self, intent: OrderIntent, strategy: str = "unknown", execution_id: str = "") -> Fill:
        symbol = intent.symbol
        side = intent.side
        size = intent.size
        price = intent.max_price
        venue = intent.venue
        fee = size * price * self.FEE_BPS / 10_000.0
        if side == "buy":
            cost = size * price + fee
            proceeds = 0.0
        else:
            cost = 0.0
            proceeds = size * price - fee
        hyp_pnl = (proceeds - cost) if side == "sell" else -(cost)
        trade = ShadowTrade(
            execution_id=execution_id or intent.id, strategy=strategy,
            venue=venue, symbol=symbol, side=side, size=size, price=price,
            fee=fee, cost=cost, proceeds=proceeds, timestamp=time.time(),
            hyp_pnl=hyp_pnl, metadata=dict(intent.metadata or {}),
        )
        self._trades.append(trade)
        cumulative = self._equity_curve[-1] + hyp_pnl
        self._equity_curve.append(cumulative)
        self._peak_pnl = max(self._peak_pnl, cumulative)
        return Fill(venue=venue, symbol=symbol, side=side, size=size,
            price=price, fee=fee, cost=cost, proceeds=proceeds,
            order_id=intent.id, status="filled",
            metadata={"shadow": True, "strategy": strategy, **trade.metadata})

    def snapshot(self, strategy: str = "") -> ShadowSnapshot:
        trades = self._trades
        if strategy:
            trades = [t for t in trades if t.strategy == strategy]
        if not trades:
            return ShadowSnapshot(strategy=strategy, total_trades=0, total_pnl=0.0,
                total_fees=0.0, win_rate=0.0, avg_trade_pnl=0.0, max_drawdown_pct=0.0,
                sharpe=0.0, equity_curve=[])
        pnls = [t.hyp_pnl for t in trades]
        total_pnl = sum(pnls)
        total_fees = sum(t.fee for t in trades)
        wins = sum(1 for p in pnls if p > 0)
        win_rate = wins / len(pnls) * 100
        avg_pnl = total_pnl / len(pnls)
        peak = 0.0
        max_dd = 0.0
        cumulative = 0.0
        for p in pnls:
            cumulative += p
            peak = max(peak, cumulative)
            dd = (peak - cumulative) / peak if peak > 0 else 0.0
            max_dd = max(max_dd, dd)
        if len(pnls) > 1:
            mean_pnl = avg_pnl
            variance = sum((p - mean_pnl) ** 2 for p in pnls) / (len(pnls) - 1)
            std_pnl = variance ** 0.5
            sharpe = (mean_pnl / std_pnl) if std_pnl > 0 else 0.0
        else:
            sharpe = 0.0
        return ShadowSnapshot(strategy=strategy, total_trades=len(trades),
            total_pnl=round(total_pnl, 6), total_fees=round(total_fees, 6),
            win_rate=round(win_rate, 2), avg_trade_pnl=round(avg_pnl, 6),
            max_drawdown_pct=round(max_dd * 100, 4), sharpe=round(sharpe, 4),
            equity_curve=self._equity_curve)

    def compare_with_paper(self, paper_fills: List[Fill]) -> Dict[str, Any]:
        shadow_pnl = sum(t.hyp_pnl for t in self._trades)
        paper_pnl = sum((f.proceeds - f.cost) for f in paper_fills if f.status == "filled")
        paper_fees = sum(f.fee for f in paper_fills)
        return {"shadow_trades": len(self._trades), "shadow_pnl": round(shadow_pnl, 6),
            "shadow_fees": round(sum(t.fee for t in self._trades), 6),
            "paper_trades": len(paper_fills), "paper_pnl": round(paper_pnl, 6),
            "paper_fees": round(paper_fees, 6),
            "pnl_difference": round(shadow_pnl - paper_pnl, 6),
            "fee_difference": round(sum(t.fee for t in self._trades) - paper_fees, 6),
            "slippage_estimate": round(paper_pnl - shadow_pnl, 6)}

    def reset(self) -> None:
        self._trades.clear()
        self._equity_curve = [0.0]
        self._peak_pnl = 0.0
