"""Ledger — the source of truth for positions, fills, and P&L.

Accounting rule: only venue-CONFIRMED fills are recorded. Estimates are
never used for P&L. The ledger is append-only and restart-safe.

Flow:
    INTENDED ORDER  ->  EXCHANGE  ->  ACTUAL FILLS  ->  VWAP / fees / slippage
                                                             |
                                                          LEDGER

Responsibilities:
  * record every confirmed Fill (paper or live)
  * maintain per-venue / per-asset positions
  * compute realized P&L (closed round-trips), unrealized P&L, NAV
  * reconcile internal positions against venue-reported balances
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional, Tuple

from config import Settings
from trading.core import Fill

log = logging.getLogger(__name__)


class ReconciliationReport:
    """Result of comparing ledger positions against venue balances."""

    def __init__(self) -> None:
        self.matches: List[Tuple[str, str, float, float]] = []
        self.discrepancies: List[Dict[str, Any]] = []

    @property
    def is_clean(self) -> bool:
        return not self.discrepancies

    def summary(self) -> Dict[str, Any]:
        return {
            "clean": self.is_clean,
            "matches": len(self.matches),
            "discrepancies": self.discrepancies,
        }


class Ledger:
    """Append-only confirmed-fill ledger with reconciliation."""

    def __init__(self, settings: Settings, path: Optional[str] = None) -> None:
        self._settings = settings
        self._path = path or os.path.join(
            getattr(settings, "memory_dir", ".memory"), "ledger.jsonl"
        )
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        self._positions: Dict[str, Dict[str, float]] = {}
        self._fills: List[Dict[str, Any]] = []
        self._realized_pnl: float = 0.0
        self._fees_paid: float = 0.0
        self._load()

    # -- Persistence ---------------------------------------------------

    def _load(self) -> None:
        """Rebuild state from the append-only file (restart-safe)."""
        if not os.path.exists(self._path):
            return
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    try:
                        self._apply_fill_entry(entry)
                    except Exception as exc:
                        # One malformed entry must never abort the replay.
                        log.warning("Ledger skipped malformed entry: %s", exc)
        except Exception as exc:
            log.error("Ledger load failed: %s", exc)

    def _append(self, entry: Dict[str, Any]) -> None:
        try:
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception as exc:
            log.error("Ledger write failed: %s", exc)

    # -- Recording -----------------------------------------------------

    def record_fill(self, fill: Fill, strategy: str = "?",
                    permit_id: str = "") -> Dict[str, Any]:
        """Record a venue-confirmed fill. The fill IS the truth."""
        entry = {
            "t": fill.timestamp or time.time(),
            "venue": fill.venue,
            "symbol": fill.symbol,
            "side": fill.side,
            "size": fill.size,
            "price": fill.price,
            "fee": fill.fee,
            "cost": fill.cost,
            "proceeds": fill.proceeds,
            "status": fill.status,
            "slippage_bps": fill.slippage_bps,
            "order_id": fill.order_id,
            "strategy": strategy,
            "permit_id": permit_id,
            "metadata": dict(getattr(fill, "metadata", {}) or {}),
        }
        self._fills.append(entry)
        self._apply_fill_entry(entry)
        self._append(entry)
        return entry

    def _apply_fill_entry(self, entry: Dict[str, Any]) -> None:
        """Apply a ledger entry during replay (idempotent).

        * Fill entries rebuild positions, fees and the fill history.
        * ``round_trip`` summary entries restore realized PnL (PnL is
          computed live from the two fills, but persisted so a replay
          reproduces it without re-deriving the match).
        * Anything else is audit metadata and is skipped.
        """
        etype = entry.get("type")
        if etype == "round_trip":
            self._realized_pnl += float(entry.get("pnl", 0.0))
            return
        if etype not in (None, "fill"):
            return
        if "venue" not in entry or "symbol" not in entry:
            return
        self._fills.append(entry)
        venue, symbol = entry["venue"], entry["symbol"]
        side, size = entry["side"], entry["size"]
        base, quote = symbol.split("/")
        self._fees_paid += entry.get("fee", 0.0)

        pos = self._positions.setdefault(venue, {})
        if side == "buy":
            pos[base] = pos.get(base, 0.0) + size
            pos[quote] = pos.get(quote, 0.0) - entry.get("cost", 0.0)
        else:
            pos[base] = pos.get(base, 0.0) - size
            pos[quote] = pos.get(quote, 0.0) + entry.get("proceeds", 0.0)

    def record_round_trip(self, buy_fill: Fill, sell_fill: Fill,
                          strategy: str = "?") -> float:
        """Book realized P&L for a completed round-trip.

        NOTE: fills themselves must be recorded separately via record_fill
        (the ExecutionCoordinator does this per leg). This method only
        books the realized P&L and appends the round-trip marker, so
        positions are never double-counted.
        """
        pnl = sell_fill.proceeds - buy_fill.cost
        self._realized_pnl += pnl
        self._append({
            "t": time.time(), "type": "round_trip", "strategy": strategy,
            "symbol": buy_fill.symbol, "pnl": round(pnl, 8),
        })
        return pnl

    # -- Queries -------------------------------------------------------

    @property
    def positions(self) -> Dict[str, Dict[str, float]]:
        return {v: dict(assets) for v, assets in self._positions.items()}

    @property
    def realized_pnl(self) -> float:
        return self._realized_pnl

    @property
    def fees_paid(self) -> float:
        return self._fees_paid

    @property
    def fill_count(self) -> int:
        return len(self._fills)

    @property
    def fills(self) -> Tuple[Dict[str, Any], ...]:
        """Read-only snapshot of all recorded fill entries.

        Entries are persisted fill dicts (see ``record_fill``), returned as a
        tuple so callers cannot mutate the ledger's history.
        """
        return tuple(self._fills)
    def unrealized_pnl(self, mark_prices: Dict[str, float]) -> float:
        """Mark all open positions to USD.

        mark_prices maps "BASE/USDT" -> price (also accepts "BASE").
        Stablecoins count at face value.
        """
        default_quote = getattr(self._settings, "default_quote", "USDT")
        stablecoins = {"USDT", "USDC", default_quote}
        total = 0.0
        for assets in self._positions.values():
            for asset, qty in assets.items():
                if qty <= 0:
                    continue
                if asset in stablecoins:
                    total += qty
                else:
                    price = (
                        mark_prices.get(f"{asset}/USDT")
                        or mark_prices.get(f"{asset}/{default_quote}")
                        or mark_prices.get(asset)
                        or 0.0
                    )
                    total += qty * price
        return total

    def nav(self, mark_prices: Dict[str, float]) -> float:
        """Net asset value: marked positions + realized P&L."""
        return self.unrealized_pnl(mark_prices) + self._realized_pnl

    def reconcile(
        self,
        venue_balances: Dict[str, Dict[str, float]],
        tolerance: float = 1e-6,
    ) -> ReconciliationReport:
        """Compare ledger positions against venue-reported balances."""
        report = ReconciliationReport()
        all_venues = set(self._positions) | set(venue_balances)
        for venue in all_venues:
            ours = self._positions.get(venue, {})
            theirs = venue_balances.get(venue, {})
            for asset in set(ours) | set(theirs):
                a, b = ours.get(asset, 0.0), theirs.get(asset, 0.0)
                if abs(a - b) <= tolerance:
                    report.matches.append((venue, asset, a, b))
                else:
                    report.discrepancies.append({
                        "venue": venue, "asset": asset,
                        "ledger": round(a, 8), "venue_reported": round(b, 8),
                        "diff": round(b - a, 8),
                    })
        return report

    def summary(self, mark_prices: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
        mark_prices = mark_prices or {}
        return {
            "fills": self.fill_count,
            "realized_pnl": round(self._realized_pnl, 6),
            "fees_paid": round(self._fees_paid, 6),
            "unrealized_usd": round(self.unrealized_pnl(mark_prices), 6),
            "nav_usd": round(self.nav(mark_prices), 6),
        }
