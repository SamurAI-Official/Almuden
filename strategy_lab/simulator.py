"""Monte Carlo simulator — resamples historical trades to generate PnL distributions."""
from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import pandas as pd

from config import Settings

log = logging.getLogger(__name__)


@dataclass
class SimulationResult:
    """Results from a Monte Carlo simulation."""
    strategy: str
    num_draws: int
    initial_balance: float
    final_balances: List[float] = field(default_factory=list)
    metrics: Dict[str, float] = field(default_factory=dict)
    percentiles: Dict[str, float] = field(default_factory=dict)

    @property
    def mean_final_balance(self) -> float:
        return sum(self.final_balances) / len(self.final_balances) if self.final_balances else 0.0

    @property
    def median_final_balance(self) -> float:
        if not self.final_balances:
            return 0.0
        sorted_balances = sorted(self.final_balances)
        mid = len(sorted_balances) // 2
        return sorted_balances[mid]

    @property
    def worst_case(self) -> float:
        return min(self.final_balances) if self.final_balances else 0.0

    @property
    def best_case(self) -> float:
        return max(self.final_balances) if self.final_balances else 0.0

    @property
    def probability_of_profit(self) -> float:
        if not self.final_balances:
            return 0.0
        profitable = sum(1 for b in self.final_balances if b > self.initial_balance)
        return profitable / len(self.final_balances) * 100


class Simulator:
    """Monte Carlo simulator for strategy outcomes."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def run(
        self,
        strategy_name: str,
        trades: List[Dict],
        num_draws: int = 1000,
        initial_balance: float = 10_000.0,
        perturbation_bps: float = 5.0,
    ) -> SimulationResult:
        """Run Monte Carlo simulation.

        Args:
            strategy_name: Name of the strategy
            trades: Historical trades to resample
            num_draws: Number of simulation runs
            initial_balance: Starting balance
            perturbation_bps: Slippage/fee perturbation in bps

        Returns:
            SimulationResult with distribution statistics
        """
        if not trades:
            log.warning("No trades to simulate")
            return SimulationResult(
                strategy=strategy_name,
                num_draws=num_draws,
                initial_balance=initial_balance,
            )

        final_balances = []

        for _ in range(num_draws):
            balance = initial_balance
            # Resample trades with replacement
            num_trades = len(trades)
            for _ in range(num_trades):
                trade = random.choice(trades)
                pnl = trade.get("pnl", 0.0)

                # Perturb PnL with noise
                perturbation = random.gauss(0, perturbation_bps / 10_000.0)
                perturbed_pnl = pnl * (1 + perturbation)

                balance += perturbed_pnl
                if balance <= 0:
                    break  # Ruin

            final_balances.append(balance)

        # Calculate metrics
        result = SimulationResult(
            strategy=strategy_name,
            num_draws=num_draws,
            initial_balance=initial_balance,
            final_balances=final_balances,
        )

        result.metrics = self._calculate_metrics(final_balances, initial_balance)
        result.percentiles = self._calculate_percentiles(final_balances)

        return result

    def _calculate_metrics(
        self, final_balances: List[float], initial_balance: float
    ) -> Dict[str, float]:
        """Calculate simulation metrics."""
        if not final_balances:
            return {}

        mean = sum(final_balances) / len(final_balances)
        sorted_balances = sorted(final_balances)

        # Standard deviation
        variance = sum((b - mean) ** 2 for b in final_balances) / len(final_balances)
        std_dev = variance ** 0.5

        # Value at Risk (5%)
        var_index = int(len(sorted_balances) * 0.05)
        var_5pct = sorted_balances[var_index] if var_index < len(sorted_balances) else sorted_balances[0]

        # Probability of ruin (balance < 1% of initial)
        ruin_threshold = initial_balance * 0.01
        ruin_count = sum(1 for b in final_balances if b < ruin_threshold)

        return {
            "mean_final_balance": round(mean, 2),
            "std_dev": round(std_dev, 2),
            "mean_return_pct": round(((mean / initial_balance) - 1.0) * 100, 4),
            "var_5pct": round(var_5pct, 2),
            "probability_of_ruin_pct": round(ruin_count / len(final_balances) * 100, 2),
        }

    @staticmethod
    def _calculate_percentiles(final_balances: List[float]) -> Dict[str, float]:
        """Calculate percentiles of final balances."""
        if not final_balances:
            return {}

        sorted_balances = sorted(final_balances)
        n = len(sorted_balances)

        def percentile(p: float) -> float:
            idx = int(n * p / 100)
            idx = min(idx, n - 1)
            return sorted_balances[idx]

        return {
            "p5": round(percentile(5), 2),
            "p10": round(percentile(10), 2),
            "p25": round(percentile(25), 2),
            "p50": round(percentile(50), 2),
            "p75": round(percentile(75), 2),
            "p90": round(percentile(90), 2),
            "p95": round(percentile(95), 2),
        }