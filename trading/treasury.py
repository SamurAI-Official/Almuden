"""Treasury — the economic core of AlMuden.

Strategies never own capital. Every dollar has an accounting identity:

    Treasury
    ├── reserve              (cold, untouchable by strategies)
    ├── operating            (allocated to strategies via tiers)
    ├── experimental         (bounded blast-radius budget)
    └── realized_profits     (harvested above the high-water mark)

Compounding policy (high-water-mark):
    Only gains above the previous NAV high-water mark are deployable.
    Deployable gains split into reserve / expansion / experiment with
    configurable percentages. A drawdown below the HWM must be recovered
    before any new profit is compounded.

    Capital scales on verified risk-adjusted performance, never on a
    single lucky trade — tier promotion lives in capital_scheduler.
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, Optional

from config import Settings

log = logging.getLogger(__name__)


class Treasury:
    """Accounts for every unit of capital; strategies draw allocations."""

    def __init__(self, settings: Settings,
                 path: Optional[str] = None) -> None:
        self._settings = settings
        self._path = path or os.path.join(
            getattr(settings, "memory_dir", ".memory"), "treasury.json"
        )
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        # Compounding policy — configurable, defaults conservative.
        self.reserve_pct = getattr(settings, "treasury_reserve_pct", 50.0)
        self.expansion_pct = getattr(settings, "treasury_expansion_pct", 30.0)
        self.experiment_pct = getattr(settings, "treasury_experiment_pct", 20.0)
        total = self.reserve_pct + self.expansion_pct + self.experiment_pct
        if total > 0 and abs(total - 100.0) > 1e-9:
            # Normalize so the split always sums to 100%.
            self.reserve_pct *= 100.0 / total
            self.expansion_pct *= 100.0 / total
            self.experiment_pct *= 100.0 / total

        # Bucket state (all USD-numeraire).
        self.reserve: float = 0.0
        self.operating: float = 0.0
        self.experimental: float = 0.0
        self.realized_profits: float = 0.0
        self.high_water_mark: float = 0.0
        self.allocations: Dict[str, Dict[str, Any]] = {}

        self._load()

    # -- Persistence ---------------------------------------------------

    def _load(self) -> None:
        if not os.path.exists(self._path):
            return
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                state = json.load(f)
            self.reserve = state.get("reserve", 0.0)
            self.operating = state.get("operating", 0.0)
            self.experimental = state.get("experimental", 0.0)
            self.realized_profits = state.get("realized_profits", 0.0)
            self.high_water_mark = state.get("high_water_mark", 0.0)
            self.allocations = state.get("allocations", {})
            log.info("Treasury loaded: %s", self.summary())
        except Exception as exc:
            log.error("Treasury load failed: %s", exc)

    def _save(self) -> None:
        try:
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(self._state(), f, indent=2)
        except Exception as exc:
            log.error("Treasury save failed: %s", exc)

    def _state(self) -> Dict[str, Any]:
        return {
            "t": time.time(),
            "reserve": round(self.reserve, 6),
            "operating": round(self.operating, 6),
            "experimental": round(self.experimental, 6),
            "realized_profits": round(self.realized_profits, 6),
            "high_water_mark": round(self.high_water_mark, 6),
            "allocations": self.allocations,
        }

    # -- Initialization ------------------------------------------------

    def initialize(self, total_capital: float) -> None:
        """Seed buckets from initial capital (idempotent — only when empty)."""
        if self.operating > 0 or self.reserve > 0 or self.experimental > 0:
            return
        # Initial split: 60% operating, 35% reserve, 5% experiment.
        self.operating = round(total_capital * 0.60, 6)
        self.reserve = round(total_capital * 0.35, 6)
        self.experimental = round(total_capital * 0.05, 6)
        self.high_water_mark = total_capital
        self._save()
        log.info("Treasury initialized with %.2f", total_capital)

    # -- Allocation ----------------------------------------------------

    def available(self, kind: str = "operating") -> float:
        """Capital available for new allocation from a bucket."""
        bucket = {"operating": self.operating,
                  "experimental": self.experimental}.get(kind)
        if bucket is None:
            raise ValueError(f"unknown bucket: {kind}")
        allocated = sum(
            a["allocated"] for a in self.allocations.values()
            if a.get("kind", "operating") == kind
        )
        return max(0.0, bucket - allocated)

    def allocate(self, strategy_id: str, amount: float,
                 kind: str = "operating", tier: str = "RESEARCH") -> bool:
        """Reserve *amount* of capital for a strategy. Never over-allocates."""
        if amount <= 0:
            return False
        if amount > self.available(kind) + 1e-9:
            log.warning(
                "Allocation denied: %s wants %.2f, %.2f available in %s",
                strategy_id, amount, self.available(kind), kind,
            )
            return False
        entry = self.allocations.setdefault(strategy_id, {
            "allocated": 0.0, "pnl": 0.0, "trades": 0,
            "wins": 0, "peak_pnl": 0.0, "kind": kind, "tier": tier,
        })
        entry["allocated"] = round(entry["allocated"] + amount, 6)
        entry["tier"] = tier
        self._save()
        return True

    def release(self, strategy_id: str, amount: Optional[float] = None) -> None:
        """Return allocated capital to the operating bucket."""
        entry = self.allocations.get(strategy_id)
        if not entry:
            return
        amount = entry["allocated"] if amount is None else min(
            amount, entry["allocated"]
        )
        entry["allocated"] = round(entry["allocated"] - amount, 6)
        self._save()

    def record_return(self, strategy_id: str, pnl: float) -> None:
        """Book a closed round-trip P&L against a strategy allocation."""
        entry = self.allocations.setdefault(strategy_id, {
            "allocated": 0.0, "pnl": 0.0, "trades": 0,
            "wins": 0, "peak_pnl": 0.0, "kind": "operating",
            "tier": "RESEARCH",
        })
        entry["pnl"] = round(entry["pnl"] + pnl, 6)
        entry["trades"] += 1
        if pnl > 0:
            entry["wins"] += 1
        entry["peak_pnl"] = max(entry["peak_pnl"], entry["pnl"])
        self.realized_profits = round(self.realized_profits + pnl, 6)
        self._save()

    # -- Compounding ---------------------------------------------------

    def compound(self, current_nav: float) -> Dict[str, float]:
        """Deploy gains above the high-water mark.

        Returns the amounts deployed to {reserve, expansion, experiment}.
        Below the HWM nothing deploys: drawdown must be recovered first.
        """
        gain = current_nav - self.high_water_mark
        if gain <= 0:
            return {"reserve": 0.0, "expansion": 0.0, "experiment": 0.0}
        to_reserve = gain * self.reserve_pct / 100.0
        to_expansion = gain * self.expansion_pct / 100.0
        to_experiment = gain * self.experiment_pct / 100.0
        self.reserve = round(self.reserve + to_reserve, 6)
        self.operating = round(self.operating + to_expansion, 6)
        self.experimental = round(self.experimental + to_experiment, 6)
        self.high_water_mark = current_nav
        self._save()
        log.info(
            "Compounded %.2f: reserve +%.2f, operating +%.2f, experimental +%.2f",
            gain, to_reserve, to_expansion, to_experiment,
        )
        return {"reserve": to_reserve, "expansion": to_expansion,
                "experiment": to_experiment}

    def net_worth(self) -> float:
        """All buckets + cumulative realized P&L."""
        return self.reserve + self.operating + self.experimental

    def summary(self) -> Dict[str, Any]:
        return {
            "reserve": round(self.reserve, 2),
            "operating": round(self.operating, 2),
            "experimental": round(self.experimental, 2),
            "realized_profits": round(self.realized_profits, 2),
            "high_water_mark": round(self.high_water_mark, 2),
            "net_worth": round(self.net_worth(), 2),
            "available_operating": round(self.available("operating"), 2),
            "available_experimental": round(self.available("experimental"), 2),
            "strategies": len(self.allocations),
        }
