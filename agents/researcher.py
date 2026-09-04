"""Research agent observes markets, generates hypotheses, tests them."""
from __future__ import annotations
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from config import Settings
from memory.store import MemoryStore
from strategy_lab.walk_forward import WalkForwardAnalyzer, WalkForwardResult
from trading.strategy_lifecycle import StrategyLifecycle
log = logging.getLogger(__name__)


@dataclass
class Hypothesis:
    id: str
    strategy_id: str
    description: str
    parameter_changes: Dict[str, Any] = field(default_factory=dict)
    expected_improvement: str = ""
    created_at: float = field(default_factory=time.time)


@dataclass
class ExperimentResult:
    hypothesis_id: str
    strategy_id: str
    backtest_metrics: Dict[str, float] = field(default_factory=dict)
    walk_forward_result: Optional[WalkForwardResult] = None
    confirmed: bool = False
    conclusion: str = ""
    tested_at: float = field(default_factory=time.time)

class ResearchAgent:
    """Minimal viable research agent."""

    def __init__(self, settings: Settings, memory: Optional[MemoryStore] = None,
            lifecycle: Optional[StrategyLifecycle] = None) -> None:
        self._settings = settings
        self._memory = memory or MemoryStore(settings)
        self._lifecycle = lifecycle or StrategyLifecycle(settings)
        self._walk_forward = WalkForwardAnalyzer(settings)
        self._hypotheses: List[Hypothesis] = []
        self._experiments: List[ExperimentResult] = []

    def observe_market(self, strategy_id: str, current_metrics: Dict[str, float],
            market_conditions: Dict[str, Any]) -> Dict[str, Any]:
        """Record a market observation and generate insights."""
        issues = []
        suggestions = []
        sharpe = current_metrics.get('sharpe_ratio', 0.0)
        drawdown = current_metrics.get('max_drawdown_pct', 0.0)
        pnl = current_metrics.get('total_pnl', 0.0)
        if sharpe < 0.5 and current_metrics.get('total_trades', 0) > 20:
            issues.append(f'Low Sharpe ratio: {sharpe:.2f}')
            suggestions.append('Consider tightening entry criteria')
        if drawdown > 10.0:
            issues.append(f'High drawdown: {drawdown:.1f}%')
            suggestions.append('Consider reducing position size')
        if pnl < 0 and current_metrics.get('total_trades', 0) > 30:
            issues.append(f'Negative PnL after {current_metrics["total_trades"]} trades')
            suggestions.append('Strategy may be broken')
        observation = {'timestamp': time.time(), 'strategy_id': strategy_id,
            'metrics': current_metrics, 'market_conditions': market_conditions,
            'issues': issues, 'suggestions': suggestions}
        if issues:
            self._memory.store('observation', strategy_id,
                f'Detected {len(issues)} issues: {"; ".join(issues)}',
                data=observation, importance=0.8 if len(issues) > 1 else 0.5)
        return observation

    def generate_hypothesis(self, strategy_id: str,
            observation: Dict[str, Any]) -> Optional[Hypothesis]:
        """Generate a hypothesis based on market observation."""
        issues = observation.get('issues', [])
        if not issues:
            return None
        parameter_changes = {}
        description_parts = []
        for issue in issues:
            if 'Sharpe' in issue:
                current_edge = getattr(self._settings, 'min_edge_bps', 10.0)
                parameter_changes['min_edge_bps'] = current_edge * 1.5
                description_parts.append(f'Increase min_edge_bps to {current_edge * 1.5}')
            if 'drawdown' in issue.lower():
                parameter_changes['max_position_fraction'] = 0.05
                description_parts.append('Reduce max position fraction to 5%')
            if 'Negative PnL' in issue:
                parameter_changes['mean_reversion_mode'] = True
                description_parts.append('Enable mean-reversion filtering')
        if not description_parts:
            return None
        hypothesis = Hypothesis(id=f'hyp_{strategy_id}_{int(time.time())}',
            strategy_id=strategy_id,
            description='; '.join(description_parts),
            parameter_changes=parameter_changes,
            expected_improvement='Reduce drawdown and improve risk-adjusted returns')
        self._hypotheses.append(hypothesis)
        self._memory.store('research', strategy_id,
            f'Generated hypothesis: {hypothesis.description}',
            data={'hypothesis_id': hypothesis.id, 'changes': parameter_changes},
            importance=0.7)
        return hypothesis

    def test_hypothesis(self, hypothesis: Hypothesis, strategy,
            venue: str, symbol: str, start: str, end: str) -> ExperimentResult:
        """Test a hypothesis through backtesting and walk-forward analysis."""
        log.info('Testing hypothesis %s: %s', hypothesis.id, hypothesis.description)
        try:
            wf_result = self._walk_forward.analyze(strategy, venue, symbol, start, end,
                n_folds=5, initial_balance=10_000.0)
        except Exception as e:
            log.error('Walk-forward test failed for %s: %s', hypothesis.id, e)
            wf_result = None
        confirmed = False
        conclusion = 'No significant improvement detected'
        if wf_result and wf_result.is_robust:
            confirmed = True
            conclusion = (f'Hypothesis confirmed: walk-forward efficiency '
                f'{wf_result.walk_forward_efficiency:.2f}, '
                f'avg test return {wf_result.avg_test_return:.2f}%, '
                f'sharpe {wf_result.avg_test_sharpe:.2f}')
        elif wf_result:
            conclusion = (f'Hypothesis rejected: walk-forward efficiency '
                f'{wf_result.walk_forward_efficiency:.2f}, '
                f'robust={wf_result.is_robust}')
        result = ExperimentResult(hypothesis_id=hypothesis.id,
            strategy_id=hypothesis.strategy_id,
            walk_forward_result=wf_result,
            backtest_metrics=wf_result.aggregated_test_metrics if wf_result else {},
            confirmed=confirmed, conclusion=conclusion)
        self._experiments.append(result)
        self._memory.store('research' if confirmed else 'failure',
            hypothesis.strategy_id, conclusion,
            data={'hypothesis_id': hypothesis.id,
                'walk_forward_efficiency': wf_result.walk_forward_efficiency if wf_result else 0.0,
                'confirmed': confirmed},
            importance=0.9 if confirmed else 0.6)
        return result

    def evaluate_strategy(self, strategy_id: str, strategy,
            venue: str, symbol: str, start: str, end: str) -> Dict[str, Any]:
        """Full evaluation pipeline for a strategy."""
        state = self._lifecycle.get_state(strategy_id)
        if not state:
            return {'error': f'Strategy {strategy_id} not registered'}
        try:
            wf_result = self._walk_forward.analyze(strategy, venue, symbol, start, end, n_folds=5)
        except Exception as e:
            log.error('Evaluation failed for %s: %s', strategy_id, e)
            return {'error': str(e)}
        self._lifecycle.update_shadow_metrics(strategy_id,
            trades=wf_result.aggregated_test_metrics.get('total_trades', 0),
            pnl=wf_result.aggregated_test_metrics.get('total_return_pct', 0.0),
            sharpe=wf_result.avg_test_sharpe,
            walk_forward_efficiency=wf_result.walk_forward_efficiency,
            walk_forward_robust=wf_result.is_robust)
        promotion_target = self._lifecycle.evaluate_promotion(strategy_id)
        recommendation = 'hold'
        if promotion_target and state.is_research:
            self._lifecycle.promote(strategy_id, promotion_target)
            recommendation = f'promote_to_{promotion_target}'
        return {'strategy_id': strategy_id, 'current_level': state.deployment_level,
            'walk_forward_efficiency': wf_result.walk_forward_efficiency,
            'walk_forward_robust': wf_result.is_robust,
            'avg_test_return': wf_result.avg_test_return,
            'avg_test_sharpe': wf_result.avg_test_sharpe,
            'avg_test_drawdown': wf_result.avg_test_drawdown,
            'recommendation': recommendation}

    def get_research_summary(self) -> Dict[str, Any]:
        """Summary of all research activity."""
        return {'total_hypotheses': len(self._hypotheses),
            'total_experiments': len(self._experiments),
            'confirmed_hypotheses': sum(1 for e in self._experiments if e.confirmed),
            'rejected_hypotheses': sum(1 for e in self._experiments if not e.confirmed and e.conclusion),
            'recent_experiments': [
                {'hypothesis_id': e.hypothesis_id, 'strategy_id': e.strategy_id,
                    'confirmed': e.confirmed, 'conclusion': e.conclusion[:100]}
                for e in self._experiments[-5:]]}
