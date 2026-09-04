"""Strategy lifecycle management.

Manages the promotion and demotion of strategies through deployment levels,
from initial research to full production capital.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from config import Settings
from trading.capital_scheduler import CapitalScheduler

log = logging.getLogger(__name__)


@dataclass
class StrategyLifecycleState:
    """Current lifecycle state of a strategy."""
    strategy_id: str
    strategy_version: str
    deployment_level: str
    capital_tier: int
    shadow_trades: int = 0
    shadow_pnl: float = 0.0
    shadow_sharpe: float = 0.0
    walk_forward_efficiency: float = 0.0
    walk_forward_robust: bool = False
    paper_trades: int = 0
    paper_pnl: float = 0.0
    paper_drawdown_pct: float = 0.0
    paper_sharpe: float = 0.0
    live_trades: int = 0
    live_pnl: float = 0.0
    live_drawdown_pct: float = 0.0
    first_deployed_at: float = 0.0
    last_promoted_at: float = 0.0
    last_demoted_at: float = 0.0
    promotion_reason: str = ""
    demotion_reason: str = ""

    @property
    def can_trade_shadow(self) -> bool:
        return self.deployment_level in ("RESEARCH", "HISTORICAL", "SHADOW", "PAPER", "CANARY", "VERIFIED", "PRODUCTION", "MATURE")

    @property
    def can_trade_paper(self) -> bool:
        return self.deployment_level in ("PAPER", "CANARY", "VERIFIED", "PRODUCTION", "MATURE")

    @property
    def can_trade_live(self) -> bool:
        return self.deployment_level in ("CANARY", "VERIFIED", "PRODUCTION", "MATURE")

    @property
    def is_research(self) -> bool:
        return self.deployment_level in ("RESEARCH", "HISTORICAL")


# Deployment requirements for each level
DEPLOYMENT_REQUIREMENTS: Dict[str, Dict[str, Any]] = {
    "SHADOW": {
        "min_walk_forward_folds": 3,
        "min_walk_forward_efficiency": 0.3,
        "min_shadow_trades": 20,
        "min_shadow_sharpe": 0.0,
    },
    "PAPER": {
        "min_walk_forward_efficiency": 0.5,
        "min_walk_forward_robust": True,
        "min_shadow_trades": 50,
        "min_shadow_sharpe": 0.5,
    },
    "CANARY": {
        "min_paper_trades": 100,
        "min_paper_sharpe": 1.0,
        "max_paper_drawdown_pct": 10.0,
    },
    "VERIFIED": {
        "min_paper_trades": 200,
        "min_paper_sharpe": 1.2,
        "max_paper_drawdown_pct": 8.0,
    },
    "PRODUCTION": {
        "min_live_trades": 50,
        "max_live_drawdown_pct": 5.0,
    },
    "MATURE": {
        "min_live_trades": 200,
        "max_live_drawdown_pct": 3.0,
    },
}

class StrategyLifecycle:
    """Manages strategy promotion and demotion through deployment levels."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._states: Dict[str, StrategyLifecycleState] = {}
        self._level_order = ['RESEARCH', 'HISTORICAL', 'SHADOW', 'PAPER', 'CANARY', 'VERIFIED', 'PRODUCTION', 'MATURE']

    def register(self, strategy_id: str, version: str = '1.0.0') -> StrategyLifecycleState:
        state = StrategyLifecycleState(
            strategy_id=strategy_id, strategy_version=version,
            deployment_level='RESEARCH', capital_tier=0,
            first_deployed_at=time.time())
        self._states[strategy_id] = state
        log.info('Strategy %s v%s registered at RESEARCH', strategy_id, version)
        return state

    def get_state(self, strategy_id: str) -> Optional[StrategyLifecycleState]:
        return self._states.get(strategy_id)

    def update_shadow_metrics(self, strategy_id: str, trades: int, pnl: float,
            sharpe: float, walk_forward_efficiency: float = 0.0,
            walk_forward_robust: bool = False) -> None:
        state = self._states.get(strategy_id)
        if state:
            state.shadow_trades = trades
            state.shadow_pnl = pnl
            state.shadow_sharpe = sharpe
            state.walk_forward_efficiency = walk_forward_efficiency
            state.walk_forward_robust = walk_forward_robust

    def update_paper_metrics(self, strategy_id: str, trades: int, pnl: float,
            sharpe: float, drawdown_pct: float) -> None:
        state = self._states.get(strategy_id)
        if state:
            state.paper_trades = trades
            state.paper_pnl = pnl
            state.paper_sharpe = sharpe
            state.paper_drawdown_pct = drawdown_pct

    def update_live_metrics(self, strategy_id: str, trades: int, pnl: float,
            drawdown_pct: float) -> None:
        state = self._states.get(strategy_id)
        if state:
            state.live_trades = trades
            state.live_pnl = pnl
            state.live_drawdown_pct = drawdown_pct

    def evaluate_promotion(self, strategy_id: str) -> Optional[str]:
        state = self._states.get(strategy_id)
        if not state:
            return None
        current_idx = self._level_order.index(state.deployment_level)
        if current_idx >= len(self._level_order) - 1:
            return None
        target_level = self._level_order[current_idx + 1]
        requirements = DEPLOYMENT_REQUIREMENTS.get(target_level, {})
        if self._meets_requirements(state, requirements):
            return target_level
        return None

    def promote(self, strategy_id: str, target_level: str) -> bool:
        state = self._states.get(strategy_id)
        if not state:
            return False
        current_idx = self._level_order.index(state.deployment_level)
        target_idx = self._level_order.index(target_level)
        if target_idx <= current_idx:
            return False
        old_level = state.deployment_level
        state.deployment_level = target_level
        state.last_promoted_at = time.time()
        state.promotion_reason = f'Promoted from {old_level} to {target_level}'
        level_to_tier = {'RESEARCH': 0, 'HISTORICAL': 0, 'SHADOW': 0,
            'PAPER': 1, 'CANARY': 1, 'VERIFIED': 2, 'PRODUCTION': 3, 'MATURE': 4}
        state.capital_tier = level_to_tier.get(target_level, state.capital_tier)
        log.info('Strategy %s promoted: %s -> %s (tier %d)', strategy_id, old_level, target_level, state.capital_tier)
        return True

    def demote(self, strategy_id: str, reason: str, levels: int = 1) -> bool:
        state = self._states.get(strategy_id)
        if not state:
            return False
        current_idx = self._level_order.index(state.deployment_level)
        new_idx = max(0, current_idx - levels)
        old_level = state.deployment_level
        state.deployment_level = self._level_order[new_idx]
        state.last_demoted_at = time.time()
        state.demotion_reason = reason
        level_to_tier = {'RESEARCH': 0, 'HISTORICAL': 0, 'SHADOW': 0,
            'PAPER': 1, 'CANARY': 1, 'VERIFIED': 2, 'PRODUCTION': 3, 'MATURE': 4}
        state.capital_tier = level_to_tier.get(state.deployment_level, 0)
        log.warning('Strategy %s demoted: %s -> %s (%s)', strategy_id, old_level, state.deployment_level, reason)
        return True

    def check_health(self, strategy_id: str) -> Dict[str, Any]:
        state = self._states.get(strategy_id)
        if not state or not state.can_trade_live:
            return {'healthy': True, 'action': 'none'}
        issues = []
        max_dd = getattr(self._settings, 'max_drawdown_pct', 10.0)
        if state.live_drawdown_pct > max_dd:
            issues.append(f'drawdown {state.live_drawdown_pct:.1f}% > {max_dd}%')
        if state.live_trades > 20 and state.live_pnl < 0:
            issues.append(f'negative live PnL after {state.live_trades} trades')
        if issues:
            return {'healthy': False, 'action': 'demote', 'reason': '; '.join(issues)}
        return {'healthy': True, 'action': 'none'}

    def get_capital_allocation(self, strategy_id: str, total_capital: float) -> float:
        state = self._states.get(strategy_id)
        if not state or not state.can_trade_live:
            return 0.0
        scheduler = CapitalScheduler(self._settings, total_capital, initial_tier=state.capital_tier)
        return scheduler.get_max_capital()

    def _meets_requirements(self, state: StrategyLifecycleState, requirements: Dict[str, Any]) -> bool:
        for key, threshold in requirements.items():
            if key.startswith('min_'):
                actual = getattr(state, key.replace('min_', ''), 0)
                if isinstance(threshold, bool):
                    if actual != threshold:
                        return False
                elif actual < threshold:
                    return False
            elif key.startswith('max_'):
                actual = getattr(state, key.replace('max_', ''), float('inf'))
                if actual > threshold:
                    return False
        return True

    def summary(self) -> Dict[str, Any]:
        return {sid: {'level': s.deployment_level, 'tier': s.capital_tier,
            'shadow_trades': s.shadow_trades, 'shadow_pnl': round(s.shadow_pnl, 2),
            'paper_trades': s.paper_trades, 'paper_pnl': round(s.paper_pnl, 2),
            'live_trades': s.live_trades, 'live_pnl': round(s.live_pnl, 2)}
            for sid, s in self._states.items()}
