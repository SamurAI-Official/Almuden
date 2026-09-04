"""Capital scheduler — evidence-based capital deployment.

Replaces the old dollar-profit milestone model ($50 → 10%, $1000 → 100%)
with a **capital confidence score** derived from risk-adjusted evidence:

  score += sample_size_weight
  score += expectancy_weight
  score += profit_factor_weight
  score -= max_drawdown_penalty
  score -= execution_error_penalty
  score -= slippage_deviation_penalty

Tiers (promotion requires BOTH time and evidence; demotion is automatic):
  TIER 0 RESEARCH   — 0.00% of capital
  TIER 1 CANARY     — 0.25–1%
  TIER 2 PROBATION  — 1–3%
  TIER 3 VERIFIED   — 3–10%
  TIER 4 PRODUCTION — 10–25%
  TIER 5 MATURE     — risk-budget determined

A lucky $1,000 trade never authorizes 100% capital.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List

from config import Settings

log = logging.getLogger(__name__)

# tier_index -> (name, min_pct, max_pct) of total capital.
TIER_DEFINITIONS: List[Dict[str, Any]] = [
    {"name": "RESEARCH", "min_pct": 0.0, "max_pct": 0.0},
    {"name": "CANARY", "min_pct": 0.0, "max_pct": 1.0},
    {"name": "PROBATION", "min_pct": 1.0, "max_pct": 3.0},
    {"name": "VERIFIED", "min_pct": 3.0, "max_pct": 10.0},
    {"name": "PRODUCTION", "min_pct": 10.0, "max_pct": 25.0},
    {"name": "MATURE", "min_pct": 25.0, "max_pct": 100.0},
]

# Promotion gates — evidence AND time requirements per tier.
PROMOTION_GATES = {
    1: {"min_trades": 10, "min_age_days": 1, "min_score": 3.0},
    2: {"min_trades": 25, "min_age_days": 3, "min_score": 5.0},
    3: {"min_trades": 50, "min_age_days": 7, "min_score": 7.0},
    4: {"min_trades": 100, "min_age_days": 14, "min_score": 9.0},
    5: {"min_trades": 200, "min_age_days": 30, "min_score": 12.0},
}

# Demotion requires EVIDENCE OF HARM with a meaningful sample. A tiny
# sample (low score only because n is small) must HOLD the tier, not
# demote it — absence of evidence is not evidence of harm.
MIN_TRADES_FOR_DEMOTION = 10
DEMOTE_ON_ERROR_RATE = 0.20   # >20% of executions errored
DEMOTE_ON_DRAWDOWN = 0.20     # >20% peak-to-trough on trade equity


class CapitalScheduler:
    """Manages capital allocation with evidence-based gradual deployment."""

    def __init__(
        self,
        settings: Settings,
        total_capital: float = 10_000.0,
        initial_tier: int = 0,
    ) -> None:
        self._settings = settings
        self._total_capital = total_capital
        self._total_profit: float = 0.0
        self._trades: List[Dict[str, Any]] = []
        self._current_tier: int = initial_tier
        self._started_at: float = time.time()
        self._execution_errors: int = 0
        self._slippage_deviations: List[float] = []

    # ── Trade feed ──────────────────────────────────────────────────

    def update_profit(self, pnl: float, slippage_bps: float = 0.0) -> None:
        """Record a trade result (pnl + optional measured slippage)."""
        self._total_profit += pnl
        self._trades.append({"pnl": pnl, "slippage_bps": slippage_bps, "t": time.time()})
        if slippage_bps:
            self._slippage_deviations.append(abs(slippage_bps))
        self._recompute_tier()

    def record_execution_error(self) -> None:
        """Increment execution-error count (used for confidence penalty)."""
        self._execution_errors += 1

    # ── Confidence scoring ──────────────────────────────────────────

    def _confidence_score(self) -> float:
        """Evidence-based score. Promotion requires time AND evidence."""
        trades = self._trades
        n = len(trades)
        if n == 0:
            return 0.0

        score = 0.0

        # Sample size: 0 to +3 as trades accumulate.
        score += min(3.0, n / 50.0)

        # Live expectancy per trade (in capital %).
        avg_pnl = self._total_profit / n
        expectancy_frac = avg_pnl / max(self._total_capital, 1)
        score += max(-2.0, min(3.0, expectancy_frac * 500.0 * n / max(n, 10)))

        # Profit factor.
        wins = [t["pnl"] for t in trades if t["pnl"] > 0]
        gross_profit = sum(wins)
        gross_loss = abs(sum(t["pnl"] for t in trades if t["pnl"] <= 0))
        if gross_loss > 0:
            pf = gross_profit / gross_loss
            score += max(-1.0, min(2.0, (pf - 1.0) * 2.0))

        # Max drawdown penalty (on cumulative trade equity).
        score -= min(3.0, self._max_drawdown() * 200.0)

        # Execution-error penalty.
        error_rate = self._execution_errors / max(n, 1)
        score -= min(3.0, error_rate * 50.0)

        # Slippage deviation penalty (> 50 bps average is unacceptable).
        if self._slippage_deviations:
            avg_slip = sum(self._slippage_deviations) / len(self._slippage_deviations)
            if avg_slip > 50:
                score -= min(2.0, (avg_slip - 50.0) / 50.0)

        return max(0.0, score)

    def _max_drawdown(self) -> float:
        """Max peak-to-trough drawdown on cumulative trade equity (0..1)."""
        equity = 0.0
        peak = 0.0
        max_dd = 0.0
        for t in self._trades:
            equity += t["pnl"]
            peak = max(peak, equity)
            if peak > 0:
                dd = (peak - equity) / peak
                max_dd = max(max_dd, dd)
        return max_dd

    def _recompute_tier(self) -> None:
        """Re-evaluate the capital tier.

        - Promotion: requires BOTH time and evidence (gates above).
        - Hold: insufficient evidence keeps the current tier.
        - Demotion: requires evidence of harm (negative PnL, high error
          rate, or deep drawdown) with a meaningful sample size.
        - Paper floor: paper mode never drops below CANARY — its whole
          purpose is to build the track record that justifies promotion.
        """
        score = self._confidence_score()
        n = len(self._trades)
        age_days = (time.time() - self._started_at) / 86400.0

        # Promotion candidate: highest tier whose gates all pass.
        candidate = 0
        for tier_index in reversed(range(1, len(PROMOTION_GATES) + 1)):
            gates = PROMOTION_GATES[tier_index]
            if (
                n >= gates["min_trades"]
                and age_days >= gates["min_age_days"]
                and score >= gates["min_score"]
            ):
                candidate = tier_index
                break

        # Promotion is slow.
        if candidate > self._current_tier:
            log.info(
                "CapitalScheduler promoted: tier %s -> %s (score=%.2f, trades=%d, age=%.1fd)",
                self._current_tier, candidate, score, n, age_days,
            )
            self._current_tier = candidate
            return

        # Demotion is fast — but only on evidence of harm.
        if n >= MIN_TRADES_FOR_DEMOTION and candidate < self._current_tier:
            reasons: List[str] = []
            total_pnl = sum(t["pnl"] for t in self._trades)
            if total_pnl < 0:
                reasons.append(f"negative_pnl({total_pnl:.2f})")
            error_rate = self._execution_errors / n
            if error_rate > DEMOTE_ON_ERROR_RATE:
                reasons.append(f"error_rate({error_rate:.0%})")
            max_dd = self._max_drawdown()
            if max_dd > DEMOTE_ON_DRAWDOWN:
                reasons.append(f"drawdown({max_dd:.0%})")
            if reasons:
                log.warning(
                    "CapitalScheduler demoted: tier %s -> %s (%s; score=%.2f, "
                    "trades=%d, age=%.1fd)",
                    self._current_tier, candidate, ", ".join(reasons),
                    score, n, age_days,
                )
                self._current_tier = candidate

        # Paper mode floor: never below CANARY.
        mode = getattr(self._settings, "mode", "paper")
        if mode == "paper" and self._current_tier < 1:
            self._current_tier = 1

    # ── Capital exposure ────────────────────────────────────────────

    def get_tier_allocation_pct(self) -> float:
        """Current tier's max capital fraction (decimal, e.g. 0.01)."""
        tier = TIER_DEFINITIONS[self._current_tier]
        return tier["max_pct"] / 100.0

    def get_max_capital(self) -> float:
        """Maximum capital available for the current tier."""
        return self._total_capital * self.get_tier_allocation_pct()

    def get_max_trade_size(self) -> float:
        """Each trade uses at most 20% of allowed capital."""
        return self.get_max_capital() * 0.2

    def get_status(self) -> Dict[str, Any]:
        """Get scheduler status."""
        tier = TIER_DEFINITIONS[self._current_tier]
        return {
            "total_capital": self._total_capital,
            "total_profit": round(self._total_profit, 4),
            "current_tier": self._current_tier,
            "tier_name": tier["name"],
            "allocation_pct": tier["max_pct"],
            "max_capital": self.get_max_capital(),
            "max_trade_size": self.get_max_trade_size(),
            "confidence_score": round(self._confidence_score(), 2),
            "trade_count": len(self._trades),
            "execution_errors": self._execution_errors,
        }