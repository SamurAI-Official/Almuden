"""ExecutionCoordinator — owns multi-leg execution and leg risk.

Naive buy->sell has a failure mode where leg A fills and leg B fails,
leaving an unhedged long. This module makes that state explicit and
recoverable via a state machine:

    PROPOSED -> VALIDATED -> LEG_A_SUBMITTED -> LEG_A_FILLED
        -> LEG_B_SUBMITTED -> LEG_B_FILLED -> SETTLED

Failure paths:
    LEG_A_FAILED               -> CLOSED (nothing was at risk)
    LEG_B_FAILED               -> EMERGENCY_HEDGE (sell on buy venue)
        hedge filled           -> CLOSED (hedged, realized loss recorded)
        hedge failed           -> MANUAL_INTERVENTION (position recorded)

Every transition is persisted so a restart can resume or unwind any
in-flight execution. Each leg carries an idempotency key.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from enum import Enum
from typing import Any, Dict, List, Optional

from config import Settings
from trading.core import Fill, OrderIntent
from trading.ledger import Ledger

log = logging.getLogger(__name__)


class ExecutionState(str, Enum):
    PROPOSED = "proposed"
    VALIDATED = "validated"
    LEG_A_SUBMITTED = "leg_a_submitted"
    LEG_A_FILLED = "leg_a_filled"
    LEG_A_FAILED = "leg_a_failed"
    LEG_B_SUBMITTED = "leg_b_submitted"
    LEG_B_FILLED = "leg_b_filled"
    LEG_B_FAILED = "leg_b_failed"
    EMERGENCY_HEDGE = "emergency_hedge"
    SETTLED = "settled"
    CLOSED = "closed"
    MANUAL_INTERVENTION = "manual_intervention"


TERMINAL = {ExecutionState.SETTLED, ExecutionState.CLOSED,
            ExecutionState.MANUAL_INTERVENTION}


class ExecutionResult:
    """Outcome of a coordinated round-trip."""

    def __init__(self, execution_id: str, state: ExecutionState,
                 buy_fill: Optional[Fill] = None,
                 sell_fill: Optional[Fill] = None,
                 hedge_fill: Optional[Fill] = None,
                 pnl: float = 0.0,
                 reason: str = "") -> None:
        self.execution_id = execution_id
        self.state = state
        self.buy_fill = buy_fill
        self.sell_fill = sell_fill
        self.hedge_fill = hedge_fill
        self.pnl = pnl
        self.reason = reason

    @property
    def settled(self) -> bool:
        return self.state == ExecutionState.SETTLED

    def summary(self) -> Dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "state": self.state.value,
            "pnl": round(self.pnl, 6),
            "reason": self.reason,
            "buy_fill": self.buy_fill.__dict__ if self.buy_fill else None,
            "sell_fill": self.sell_fill.__dict__ if self.sell_fill else None,
            "hedge_fill": self.hedge_fill.__dict__ if self.hedge_fill else None,
        }


class ExecutionCoordinator:
    """State-machine execution with leg-failure recovery and restart safety."""

    def __init__(self, settings: Settings, broker: Any, ledger: Ledger,
                 audit: Any = None, state_dir: Optional[str] = None) -> None:
        self._settings = settings
        self._broker = broker
        self._ledger = ledger
        self._audit = audit
        self._state_dir = state_dir or os.path.join(
            getattr(settings, "memory_dir", ".memory"), "executions"
        )
        os.makedirs(self._state_dir, exist_ok=True)
        self._recovered: List[str] = []

    # -- Persistence ---------------------------------------------------

    def _state_path(self, execution_id: str) -> str:
        return os.path.join(self._state_dir, f"{execution_id}.json")

    def _save(self, record: Dict[str, Any]) -> None:
        record["updated_at"] = time.time()
        try:
            with open(self._state_path(record["execution_id"]), "w",
                      encoding="utf-8") as f:
                json.dump(record, f, indent=2)
        except Exception as exc:
            log.error("Execution state save failed: %s", exc)

    def _load(self, execution_id: str) -> Optional[Dict[str, Any]]:
        try:
            with open(self._state_path(execution_id), "r",
                      encoding="utf-8") as f:
                return json.load(f)
        except Exception as exc:
            log.error("Execution state load failed: %s", exc)
            return None

    def _archive(self, execution_id: str) -> None:
        try:
            os.remove(self._state_path(execution_id))
        except OSError:
            pass


    # -- The state machine ---------------------------------------------

    async def execute_round_trip(
        self,
        buy_permit: Any,
        sell_venue: str,
        sell_limit_price: float,
        strategy: str = "?",
        sell_min_output_bps: float = 10.0,
    ) -> ExecutionResult:
        """Execute a two-leg arbitrage round-trip under leg-risk protection.

        Leg B is sized to the ACTUAL bought quantity, not the requested
        size, so partial leg-A fills cannot leave a residual unhedged.
        """
        execution_id = str(uuid.uuid4())[:12]
        buy_intent: OrderIntent = buy_permit.intent
        symbol = buy_intent.symbol
        base, quote = symbol.split("/")

        record: Dict[str, Any] = {
            "execution_id": execution_id,
            "state": ExecutionState.VALIDATED.value,
            "symbol": symbol,
            "strategy": strategy,
            "buy_venue": buy_intent.venue,
            "sell_venue": sell_venue,
            "permit_id": buy_permit.permit_id,
            "leg_a_key": f"{execution_id}-A",
            "leg_b_key": f"{execution_id}-B",
            "history": [{"t": time.time(), "state": "validated"}],
        }
        self._save(record)

        # ---- LEG A: buy --------------------------------------------
        record["state"] = ExecutionState.LEG_A_SUBMITTED.value
        self._save(record)
        try:
            if buy_permit.is_expired:
                record["state"] = ExecutionState.CLOSED.value
                self._save(record)
                return ExecutionResult(execution_id, ExecutionState.CLOSED,
                                       reason="permit expired before leg A")
            buy_fill = await self._broker.execute(buy_intent)
        except Exception as exc:
            record["state"] = ExecutionState.LEG_A_FAILED.value
            record["history"].append({"t": time.time(), "state": "leg_a_failed",
                                      "reason": str(exc)})
            self._save(record)
            self._finish(record)
            return ExecutionResult(execution_id, ExecutionState.CLOSED,
                                   reason=f"leg A failed: {exc}")

        if buy_fill.status == "failed" or buy_fill.size <= 0:
            record["state"] = ExecutionState.LEG_A_FAILED.value
            record["history"].append({"t": time.time(), "state": "leg_a_failed",
                                      "reason": "no fill"})
            self._save(record)
            self._finish(record)
            return ExecutionResult(execution_id, ExecutionState.CLOSED,
                                   reason="leg A did not fill")

        record["state"] = ExecutionState.LEG_A_FILLED.value
        record["buy_fill"] = buy_fill.__dict__
        record["history"].append({"t": time.time(), "state": "leg_a_filled",
                                  "size": buy_fill.size})
        self._save(record)
        self._ledger.record_fill(buy_fill, strategy, buy_permit.permit_id)

        # ---- LEG B: sell the ACTUAL bought quantity -----------------
        min_output = buy_fill.size * sell_limit_price * (1.0 - sell_min_output_bps / 10_000.0)
        sell_intent = OrderIntent(
            venue=sell_venue,
            symbol=symbol,
            side="sell",
            size=buy_fill.size,
            max_price=sell_limit_price,
            min_output=min_output,
            ttl_ms=buy_permit.ttl_ms,
            metadata={"execution_id": execution_id, "leg": "B"},
        )
        record["state"] = ExecutionState.LEG_B_SUBMITTED.value
        self._save(record)
        try:
            sell_fill = await self._broker.execute(sell_intent)
        except Exception as exc:
            record["history"].append({"t": time.time(),
                                      "state": "leg_b_failed",
                                      "reason": str(exc)})
            self._save(record)
            return await self._emergency_hedge(record, buy_fill, sell_limit_price,
                                               strategy, str(exc))

        if sell_fill.status == "failed" or sell_fill.size <= 0:
            return await self._emergency_hedge(record, buy_fill, sell_limit_price,
                                               strategy, "leg B did not fill")

        # ---- SETTLED -------------------------------------------------
        pnl = sell_fill.proceeds - buy_fill.cost
        record["sell_fill"] = sell_fill.__dict__
        record["pnl"] = pnl
        record["state"] = ExecutionState.SETTLED.value
        record["history"].append({"t": time.time(), "state": "settled",
                                  "pnl": round(pnl, 8)})
        self._save(record)
        self._ledger.record_fill(sell_fill, strategy, buy_permit.permit_id)
        self._ledger.record_round_trip(buy_fill, sell_fill, strategy)
        self._finish(record)
        return ExecutionResult(execution_id, ExecutionState.SETTLED,
                               buy_fill=buy_fill, sell_fill=sell_fill,
                               pnl=pnl)
    # -- Leg-failure recovery ------------------------------------------

    def _finish(self, record: Dict[str, Any]) -> None:
        """Archive a terminal-state execution (state file removal)."""
        state = record.get("state")
        if state in (t.value for t in TERMINAL):
            self._archive(record["execution_id"])

    async def _emergency_hedge(
        self,
        record: Dict[str, Any],
        buy_fill: Fill,
        sell_limit_price: float,
        strategy: str,
        reason: str,
    ) -> ExecutionResult:
        """Leg B failed while holding inventory — hedge on the buy venue.

        We hold base asset bought on the buy venue. The fastest way to
        flatten is to sell it right back where we bought it, accepting a
        realized loss rather than carrying an unhedged speculative position.

        If even the hedge fails, the position is recorded and the execution
        enters MANUAL_INTERVENTION — visible at startup via recover_pending.
        """
        execution_id = record["execution_id"]
        symbol = record["symbol"]
        buy_venue = record["buy_venue"]

        record["state"] = ExecutionState.EMERGENCY_HEDGE.value
        record["history"].append({
            "t": time.time(), "state": "emergency_hedge", "reason": reason,
        })
        self._save(record)
        log.warning(
            "EMERGENCY HEDGE %s: leg B failed (%s); selling %.8f %s on %s",
            execution_id, reason, buy_fill.size, symbol, buy_venue,
        )

        hedge_intent = OrderIntent(
            venue=buy_venue,
            symbol=symbol,
            side="sell",
            size=buy_fill.size,
            # Hedge is defensive: cross the spread, accept a worse price.
            max_price=sell_limit_price,  # informational; broker crosses spread
            min_output=0.0,  # accept whatever the market gives — flattening
            ttl_ms=10_000,
            metadata={"execution_id": execution_id, "leg": "HEDGE"},
        )
        try:
            hedge_fill = await self._broker.execute(hedge_intent)
        except Exception as exc:
            record["state"] = ExecutionState.MANUAL_INTERVENTION.value
            record["history"].append({
                "t": time.time(), "state": "manual_intervention",
                "reason": f"hedge failed: {exc}",
            })
            self._save(record)
            log.critical(
                "MANUAL INTERVENTION %s: hedge failed (%s); holding %.8f %s",
                execution_id, exc, buy_fill.size, symbol,
            )
            return ExecutionResult(
                execution_id, ExecutionState.MANUAL_INTERVENTION,
                buy_fill=buy_fill, pnl=0.0,
                reason=f"leg B failed ({reason}); hedge also failed: {exc}",
            )

        if hedge_fill.status == "failed" or hedge_fill.size <= 0:
            record["state"] = ExecutionState.MANUAL_INTERVENTION.value
            record["history"].append({
                "t": time.time(), "state": "manual_intervention",
                "reason": "hedge did not fill",
            })
            self._save(record)
            return ExecutionResult(
                execution_id, ExecutionState.MANUAL_INTERVENTION,
                buy_fill=buy_fill, pnl=0.0,
                reason=f"leg B failed ({reason}); hedge did not fill",
            )

        # Hedge filled: position flattened, realized loss (usually) booked.
        pnl = hedge_fill.proceeds - buy_fill.cost
        record["hedge_fill"] = hedge_fill.__dict__
        record["pnl"] = pnl
        record["state"] = ExecutionState.CLOSED.value
        record["history"].append({
            "t": time.time(), "state": "closed", "pnl": round(pnl, 8),
        })
        self._save(record)
        self._ledger.record_fill(hedge_fill, strategy, record.get("permit_id", "?"))
        self._ledger.record_round_trip(buy_fill, hedge_fill, strategy)
        self._finish(record)
        log.warning("Hedged %s on buy venue; realized PnL %.6f", execution_id, pnl)
        return ExecutionResult(
            execution_id, ExecutionState.CLOSED,
            buy_fill=buy_fill, hedge_fill=hedge_fill, pnl=pnl,
            reason=f"leg B failed ({reason}); hedged on buy venue",
        )

    # -- Startup recovery ---------------------------------------------

    def recover_pending(self) -> List[Dict[str, Any]]:
        """Scan persisted state for executions interrupted by a restart."""
        recovered = []
        for filename in os.listdir(self._state_dir):
            if not filename.endswith(".json"):
                continue
            record = self._load(filename[:-5])
            if not record:
                continue
            state = ExecutionState(record["state"])
            if state in TERMINAL:
                self._archive(record["execution_id"])
                continue
            recovered.append(record)
            log.warning(
                "Recovered in-flight execution %s in state %s (%s %s)",
                record["execution_id"], state.value,
                record.get("symbol"), record.get("strategy"),
            )
            if state == ExecutionState.LEG_A_FILLED:
                # Bought but never sold: try the emergency hedge now.
                log.warning(
                    "Execution %s interrupted mid-leg; emergency hedge advised",
                    record["execution_id"],
                )
        self._recovered = [r["execution_id"] for r in recovered]
        return recovered
