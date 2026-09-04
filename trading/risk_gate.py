"""RiskGate — the single permitted route between strategy and broker.

The AGENT/STRATEGY never reaches the broker directly. Every execution
intent MUST pass through:

    TradeIntent
      → RiskEngine.validate()
      → CapitalScheduler.authorize()     (evidence-based sizing)
      → CircuitBreaker.assert_healthy()
      → ExecutionPermit                  (short-lived, venue-bound)
      → broker.execute(permit)

No other code path may call the broker. This module owns that boundary.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, Optional

from config import Settings
from trading.capital_scheduler import CapitalScheduler
from trading.circuit_breaker import CircuitBreaker
from trading.core import ExecutionPermit, OrderIntent
from trading.risk_engine import RiskEngine
from trading.audit import AuditLog

log = logging.getLogger(__name__)


class RiskGateError(Exception):
    """Raised when a piece of code tries to execute without a permit."""


class RiskGate:
    """Coordinates risk, capital, and circuit-breaker checks before execution."""

    def __init__(self, settings: Settings, audit: Optional[AuditLog] = None) -> None:
        self._settings = settings
        self._risk = RiskEngine(settings)
        # Paper mode starts at CANARY so the loop can build a track record.
        # Live mode starts at RESEARCH (0%) until evidence justifies promotion.
        initial_tier = 1 if getattr(settings, "mode", "paper") == "paper" else 0
        self._capital = CapitalScheduler(settings, initial_tier=initial_tier)
        self._breaker = CircuitBreaker(settings)
        self._audit = audit or AuditLog(settings)
        # Kill switch persists to disk: an engaged switch MUST survive a
        # restart (review item: "restarting AlMuden must never reset risk").
        self._kill_path = os.path.join(
            getattr(settings, "memory_dir", ".memory"), "kill_switch.json"
        )
        self._kill_engaged = self._load_kill_switch() or bool(
            getattr(settings, "live_kill_switch", False)
        )
        if self._kill_engaged:
            log.warning("KILL SWITCH ENGAGED at startup (persisted or env).")

    # -- Kill switch (hard stop) ------------------------------------------

    def _load_kill_switch(self) -> bool:
        try:
            if os.path.exists(self._kill_path):
                with open(self._kill_path, "r", encoding="utf-8") as fh:
                    return bool(json.load(fh).get("engaged", False))
        except (OSError, ValueError) as exc:
            log.error("Failed to load kill-switch state: %s", exc)
        return False

    def _save_kill_switch(self) -> None:
        try:
            os.makedirs(os.path.dirname(self._kill_path), exist_ok=True)
            with open(self._kill_path, "w", encoding="utf-8") as fh:
                json.dump({"engaged": self._kill_engaged}, fh)
        except OSError as exc:
            log.error("Failed to persist kill-switch state: %s", exc)

    @property
    def kill_switch_engaged(self) -> bool:
        return self._kill_engaged

    def engage_kill_switch(self, reason: str = "manual") -> None:
        """Engage the hard stop. Idempotent; persists across restarts."""
        if not self._kill_engaged:
            self._kill_engaged = True
            self._save_kill_switch()
            self._audit.record("kill_switch_engaged", {"reason": reason})
            log.warning("KILL SWITCH ENGAGED: %s", reason)

    def disengage_kill_switch(self, reason: str = "manual") -> None:
        """Clear the hard stop. Requires explicit operator action."""
        if self._kill_engaged:
            self._kill_engaged = False
            self._save_kill_switch()
            self._audit.record("kill_switch_disengaged", {"reason": reason})
            log.warning("Kill switch disengaged: %s", reason)

    # ── Readable status ─────────────────────────────────────────────

    @property
    def risk_engine(self) -> RiskEngine:
        return self._risk

    @property
    def capital_scheduler(self) -> CapitalScheduler:
        return self._capital

    @property
    def circuit_breaker(self) -> CircuitBreaker:
        return self._breaker

    # ── Portfolio informers ─────────────────────────────────────────

    def update_equity(self, equity: float) -> None:
        """Feed current portfolio NAV so drawdown tracking stays fresh."""
        self._breaker.update_equity(equity)

    def provide_mark_prices(self, prices: Dict[str, float]) -> None:
        """Supply mark prices so venue exposure is USD-denominated."""
        self._mark_prices = dict(prices)

    # ── The gate ────────────────────────────────────────────────────

    async def authorize(
        self,
        opportunity: Any,
        venue: str,
        symbol: str,
        side: str,
        size: float,
        limit_price: float,
        current_equity: float,
        current_positions: Dict[str, Dict[str, float]],
        mark_prices: Optional[Dict[str, float]] = None,
    ) -> Optional[ExecutionPermit]:
        """Run every pre-trade gate. Returns a valid permit or None."""
        # Hard kill switch: engaged => no permit, ever. Property (review
        # item 19): a kill switch can never be bypassed at the gate.
        if self.kill_switch_engaged or getattr(
            self._settings, "live_kill_switch", False
        ):
            self._audit.record("risk_deny", {
                "reason": "kill_switch_engaged",
                "symbol": symbol, "strategy": (
                    getattr(opportunity, "strategy", None)
                    or (opportunity.get("strategy")
                        if isinstance(opportunity, dict) else None)
                    or "?"
                ),
            })
            return None
        mark_prices = mark_prices or getattr(self, "_mark_prices", {})
        # Cap size by what capital allocation allows.
        max_capital = self._capital.get_max_capital()
        max_trade_size = self._capital.get_max_trade_size()
        capped_size = min(size, max_trade_size)
        # strategy: support both objects and dicts.
        strategy = (
            getattr(opportunity, "strategy", None)
            or (opportunity.get("strategy") if isinstance(opportunity, dict) else None)
            or "?"
        )
        if capped_size <= 0:
            self._audit.record("risk_deny", {
                "reason": "capital_allocation_zero",
                "symbol": symbol, "strategy": strategy,
            })
            return None

        if self._breaker.is_tripped:
            self._audit.record("risk_deny", {
                "reason": f"circuit_breaker: {self._breaker.trip_reason}",
                "symbol": symbol,
            })
            return None

        # Risk engine gate (drawdown, daily loss, exposure, sizes).
        check = self._risk.check_trade(
            opportunity, current_equity, current_positions, mark_prices
        )
        if not check.approved:
            self._audit.record("risk_deny", {
                "reason": check.reason,
                "symbol": symbol,
                "strategy": getattr(opportunity, "strategy", "?"),
            })
            return None

        permit = ExecutionPermit(
            intent=OrderIntent(
                venue=venue,
                symbol=symbol,
                side=side,
                size=capped_size,
                max_price=limit_price,
                ttl_ms=self._settings.permit_ttl_ms,
            ),
            approved_size=capped_size,
            approved_by="RiskGate",
            ttl_ms=self._settings.permit_ttl_ms,
        )
        self._risk.record_order_opened()
        self._audit.record("risk_approve", {
            "permit_id": permit.permit_id,
            "symbol": symbol,
            "venue": venue,
            "side": side,
            "size": capped_size,
            "strategy": strategy,
            "capital_tier": self._capital.get_status().get("tier_name"),
        })
        return permit

    def release(self, permit: ExecutionPermit, pnl: float) -> None:
        """Called after execution: record P&L, update capital, close order."""
        self._risk.record_trade_result(pnl)
        self._risk.record_order_closed()
        self._capital.update_profit(pnl)
        self._breaker.record_trade(pnl)
        self._audit.record("trade_result", {
            "permit_id": permit.permit_id,
            "pnl": round(pnl, 6),
            "capital_tier": self._capital.get_status().get("tier_name"),
        })

    def record_error(self) -> None:
        """Record an execution/API error into the breaker (sliding window)."""
        self._breaker.record_error()

    def get_status(self) -> Dict[str, Any]:
        """Combined status for API/logging."""
        return {
            "kill_switch_engaged": self.kill_switch_engaged,
            "risk": self._risk.get_status(),
            "capital": self._capital.get_status(),
            "circuit_breaker": self._breaker.get_status(),
        }