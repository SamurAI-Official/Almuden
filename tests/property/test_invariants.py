"""Money invariants — properties that must hold for EVERY input.

Run: python tests\property\test_invariants.py
"""
from __future__ import annotations

import math
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from config import load_settings  # noqa: E402
from trading.core import ExecutionPermit, Fill, OrderIntent  # noqa: E402
from trading.circuit_breaker import CircuitBreaker  # noqa: E402
from trading.ledger import Ledger  # noqa: E402
from trading.portfolio import Portfolio  # noqa: E402

os.environ.setdefault("ALMUDEN_MODE", "paper")


# ── Fixtures ─────────────────────────────────────────────────────────

def _settings(tmp: str):
    s = load_settings(".env.example")
    s.memory_dir = os.path.join(tmp, "memory")
    return s


def _buy_fill(order_id: str = "o-1", venue: str = "kucoin",
              size: float = 10.0, price: float = 1.0, fee: float = 0.01) -> Fill:
    return Fill(venue=venue, symbol="ERG/USDT", side="buy", size=size, price=price,
                fee=fee, cost=size * price + fee, order_id=order_id, status="filled")


def _sell_fill(order_id: str = "o-2", venue: str = "mexc",
               size: float = 10.0, price: float = 1.02, fee: float = 0.01) -> Fill:
    return Fill(venue=venue, symbol="ERG/USDT", side="sell", size=size, price=price,
                fee=fee, proceeds=size * price - fee, order_id=order_id, status="filled")


# ── Property: sizes can never be non-finite ──────────────────────────

def test_fill_rejects_non_finite_size() -> None:
    for bad in (float("nan"), float("inf"), float("-inf")):
        try:
            Fill(venue="v", symbol="S/T", side="buy", size=bad, price=1.0)
            raise AssertionError(f"Fill accepted non-finite size {bad}")
        except ValueError as exc:
            assert "size" in str(exc)
    print("OK fill: non-finite size rejected")


def test_fill_rejects_non_finite_price_and_fee() -> None:
    try:
        Fill(venue="v", symbol="S/T", side="buy", size=1.0, price=float("nan"))
        raise AssertionError("Fill accepted NaN price")
    except ValueError:
        pass
    try:
        Fill(venue="v", symbol="S/T", side="buy", size=1.0, price=1.0, fee=float("inf"))
        raise AssertionError("Fill accepted infinite fee")
    except ValueError:
        pass
    print("OK fill: non-finite price/fee rejected")


def test_order_intent_rejects_non_finite_size() -> None:
    try:
        OrderIntent(venue="v", symbol="S/T", side="buy", size=float("nan"))
        raise AssertionError("OrderIntent accepted NaN size")
    except ValueError:
        pass
    print("OK intent: non-finite size rejected")


def test_fill_positions_reconcile_to_balance_changes() -> None:
    """PnL ledger must reconcile to balance changes: buy 10 @1.0 (fee .01),
    sell 10 @1.02 (fee .01) -> realized = 0.18; base delta = 0; the quote
    delta across venues equals realized PnL."""
    with tempfile.TemporaryDirectory() as tmp:
        ledger = Ledger(_settings(tmp))
        buy, sell = _buy_fill(), _sell_fill()
        ledger.record_fill(buy, "cross_venue", "perm")
        ledger.record_fill(sell, "cross_venue", "perm")
        realized = ledger.record_round_trip(buy, sell, "cross_venue")
        expected = sell.proceeds - buy.cost  # 10.19 - 10.01 = 0.18
        assert abs(realized - expected) < 1e-9, realized
        assert abs(ledger.realized_pnl - expected) < 1e-9, ledger.realized_pnl
        # Fees must be the sum of both legs, never negative.
        assert abs(ledger.fees_paid - (buy.fee + sell.fee)) < 1e-9
        assert ledger.fees_paid >= 0
        # Position reconciliation: aggregate base flat (round trip); per-venue
        # inventory reflects the legs (flattening is the rebalancer's job).
        pos = ledger.positions
        erg_total = pos["kucoin"].get("ERG", 0) + pos["mexc"].get("ERG", 0)
        assert abs(erg_total - 0.0) < 1e-9, pos
        assert abs(pos["kucoin"].get("ERG", 0) - buy.size) < 1e-9, pos
        assert abs(pos["mexc"].get("ERG", 0) + sell.size) < 1e-9, pos
        assert abs(pos["kucoin"].get("USDT", 0) + buy.cost) < 1e-9, pos
        assert abs(pos["mexc"].get("USDT", 0) - sell.proceeds) < 1e-9, pos
        # Realized PnL matches the aggregate quote balance delta.
        quote_delta = pos["mexc"].get("USDT", 0) + pos["kucoin"].get("USDT", 0)
        assert abs(quote_delta - expected) < 1e-9, quote_delta
    print("OK ledger: positions reconcile to balance changes")


def test_ledger_double_fill_is_idempotent() -> None:
    """'Transaction lands twice' can never double-count positions."""
    with tempfile.TemporaryDirectory() as tmp:
        ledger = Ledger(_settings(tmp))
        buy = _buy_fill(order_id="dup-1")
        ledger.record_fill(buy, "cross_venue", "perm")
        # Same venue + same order_id reported again — must be ignored.
        ledger.record_fill(buy, "cross_venue", "perm")
        assert ledger.fill_count == 1, f"expected 1 fill, got {ledger.fill_count}"
        assert abs(ledger.positions["kucoin"].get("ERG", 0) - 10.0) < 1e-9, (
            ledger.positions
        )
    print("OK ledger: double-reported fill applied once")


def test_ledger_positions_always_finite() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ledger = Ledger(_settings(tmp))
        ledger.record_fill(_buy_fill(order_id="f1", size=3.0), "s", "p")
        ledger.record_fill(_sell_fill(order_id="f2", size=1.5, price=2.0), "s", "p")
        for assets in ledger.positions.values():
            for qty in assets.values():
                assert isinstance(qty, (int, float)) and math.isfinite(qty), qty
        assert math.isfinite(ledger.unrealized_pnl({}))
        assert math.isfinite(ledger.realized_pnl)
    print("OK ledger: positions and PnL always finite")


def test_ledger_restart_replays_identically() -> None:
    """Restart safety: a fresh Ledger over the same file must reproduce
    identical positions and PnL (idempotent replay)."""
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "ledger.jsonl")
        s1 = _settings(tmp)
        ledger1 = Ledger(s1, path=path)
        ledger1.record_fill(_buy_fill(order_id="r1"), "s", "p")
        ledger1.record_fill(_sell_fill(order_id="r2"), "s", "p")
        ledger1.record_round_trip(_buy_fill(order_id="r1"), _sell_fill(order_id="r2"))
        pnl1 = ledger1.realized_pnl
        fills1 = ledger1.fill_count
        pos1 = ledger1.positions

        ledger2 = Ledger(s1, path=path)  # replay from disk
        assert abs(ledger2.realized_pnl - pnl1) < 1e-9, (ledger2.realized_pnl, pnl1)
        assert ledger2.fill_count == fills1, (ledger2.fill_count, fills1)
        assert ledger2.positions == pos1
    print("OK ledger: restart replay is identical")


# ── Property: portfolio math ─────────────────────────────────────────

def test_portfolio_math_invariants() -> None:
    balances = {
        "kucoin": {"USDT": 50_000, "ERG": 500, "XMR": 12},
        "mexc": {"USDT": 30_000},
    }
    marks = {"ERG": 0.26, "XMR": 523.0}
    p = Portfolio(balances, marks)
    total = p.total_value
    gross = p.gross_exposure
    net = p.net_exposure
    assert math.isfinite(total) and math.isfinite(gross) and math.isfinite(net)
    # Triangle inequality: |net| <= gross always.
    assert abs(net) <= gross + 1e-9, (net, gross)
    # Gross >= total with positive marks (longs all add).
    assert gross >= total - 1e-9, (gross, total)
    # Stablecoin exposure <= gross.
    assert p.stablecoin_exposure() <= gross + 1e-9
    # Venue exposure sums to total.
    assert abs(sum(p.venue_exposure().values()) - total) < 1e-6
    print("OK portfolio: gross/net/total invariants hold")


def test_portfolio_unknown_asset_mark_is_safe() -> None:
    """An unknown asset (no mark price) must not inflate exposure or break
    totals — it marks to 0 so it never creates phantom value."""
    balances = {"kucoin": {"USDT": 1_000, "SOMEGHOST_TOKEN": 999_999}}
    p = Portfolio(balances, {"ERG": 0.26})
    assert abs(p.total_value - 1_000.0) < 1e-9, p.total_value
    print("OK portfolio: unknown assets mark to zero safely")


# ── Property: circuit breaker sliding window ─────────────────────────

def test_circuit_breaker_errors_age_out() -> None:
    s = _settings(tempfile.mkdtemp())
    breaker = CircuitBreaker(s)
    breaker._error_window_seconds = 60
    now = time.time()
    # 5 old errors (beyond the window) + 3 fresh.
    breaker._error_timestamps.extend([now - 120] * 5)
    breaker._error_timestamps.extend([now - 1] * 3)
    assert breaker.current_error_rate == 3, breaker.current_error_rate
    # Old errors can never accumulate into a trip.
    assert breaker.is_tripped is False
    print("ok breaker: sliding-window errors age out")


def test_circuit_breaker_trips_on_windows() -> None:
    s = _settings(tempfile.mkdtemp())
    s.max_errors_per_minute = 10
    breaker = CircuitBreaker(s)
    now = time.time()
    breaker._error_timestamps.extend([now] * 10)
    breaker.record_error()  # 11th error exceeds the threshold
    assert breaker.is_tripped is True
    assert "Error rate exceeded" in breaker.trip_reason
    # Consecutive losses trip too.
    breaker.reset()
    for _ in range(s.max_consecutive_losses):
        breaker.record_trade(-1.0)
    assert breaker.is_tripped
    assert "losses" in breaker.trip_reason
    print("OK breaker: rate + consecutive-loss tripping")


# ── Property: risk gate hard invariants ──────────────────────────────

def test_kill_switch_can_never_execute() -> None:
    """Review item 19: 'kill switch can never execute'."""
    from trading.audit import AuditLog
    from trading.risk_gate import RiskGate

    with tempfile.TemporaryDirectory() as tmp:
        s = _settings(tmp)
        s.live_kill_switch = True
        gate = RiskGate(s, audit=AuditLog(s, log_path=os.path.join(tmp, "audit.jsonl")))
        import asyncio
        permit = asyncio.run(gate.authorize(
            opportunity={"strategy": "cross_venue"},
            venue="kucoin", symbol="ERG/USDT", side="buy", size=100,
            limit_price=0.26, current_equity=10000, current_positions={},
        ))
        assert permit is None, "kill switch returned a permit!"
    print("OK gate: kill switch can never execute")


def test_tripped_breaker_can_never_execute() -> None:
    from trading.audit import AuditLog
    from trading.risk_gate import RiskGate

    with tempfile.TemporaryDirectory() as tmp:
        s = _settings(tmp)
        gate = RiskGate(s, audit=AuditLog(s, log_path=os.path.join(tmp, "audit.jsonl")))
        now = time.time()
        gate.circuit_breaker._error_timestamps.extend([now] * s.max_errors_per_minute)
        gate.circuit_breaker.record_error()
        assert gate.circuit_breaker.is_tripped
        import asyncio
        permit = asyncio.run(gate.authorize(
            opportunity={"strategy": "cross_venue"},
            venue="kucoin", symbol="ERG/USDT", side="buy", size=100,
            limit_price=0.26, current_equity=10000, current_positions={},
        ))
        assert permit is None, "tripped breaker returned a permit!"
    print("OK gate: tripped circuit breaker can never execute")


def test_zero_capital_can_never_execute() -> None:
    """Trade cannot exceed allocated capital: at tier RESEARCH (0%) no
    permit is ever produced."""
    from trading.audit import AuditLog
    from trading.risk_gate import RiskGate

    with tempfile.TemporaryDirectory() as tmp:
        s = _settings(tmp)
        s.mode = "live"  # live bootstraps at RESEARCH = 0% capital
        gate = RiskGate(s, audit=AuditLog(s, log_path=os.path.join(tmp, "audit.jsonl")))
        import asyncio
        permit = asyncio.run(gate.authorize(
            opportunity={"strategy": "cross_venue"},
            venue="kucoin", symbol="ERG/USDT", side="buy", size=100,
            limit_price=0.26, current_equity=10000, current_positions={},
            mark_prices={"ERG": 0.26},
        ))
        assert permit is None, "RESEARCH tier produced a permit!"
    print("OK gate: zero capital allocation can never execute")


def test_expired_permit_can_never_execute() -> None:
    """An expired permit is unusable — short-lived by construction."""
    intent = OrderIntent(venue="kucoin", symbol="ERG/USDT", side="buy", size=10)
    permit = ExecutionPermit(intent, approved_size=10, ttl_ms=10)
    permit.timestamp = time.time() - 60  # force expiry
    assert permit.is_expired is True
    print("OK permit: expired permits are unusable")


# ── Property: transaction validator fail-closed ─────────────────────

def test_transaction_validator_can_never_approve_without_solders() -> None:
    from trading.venues.solana.transaction_validator import (
        SOLDERS_AVAILABLE,
        ExpectedSwap,
        TransactionValidator,
    )

    validator = TransactionValidator()
    expected = ExpectedSwap(
        taker_pubkey="x", input_mint="y", output_mint="z",
        in_amount_raw=1, min_out_raw=1, max_priority_fee_lamports=1,
    )
    # Garbage input. Without solders this MUST reject; with solders it must
    # still reject (it is garbage). The property is: never approve garbage.
    result = validator.validate("not-a-tx", expected)
    assert result.approved is False
    if not SOLDERS_AVAILABLE:
        assert any("PARSER_UNAVAILABLE" == i.code for i in result.issues)
    print(f"OK validator: fail-closed (solders={SOLDERS_AVAILABLE})")


# ── Entrypoint ───────────────────────────────────────────────────────

def main() -> None:
    test_fill_rejects_non_finite_size()
    test_fill_rejects_non_finite_price_and_fee()
    test_order_intent_rejects_non_finite_size()
    test_fill_positions_reconcile_to_balance_changes()
    test_ledger_double_fill_is_idempotent()
    test_ledger_positions_always_finite()
    test_ledger_restart_replays_identically()
    test_portfolio_math_invariants()
    test_portfolio_unknown_asset_mark_is_safe()
    test_circuit_breaker_errors_age_out()
    test_circuit_breaker_trips_on_windows()
    test_kill_switch_can_never_execute()
    test_tripped_breaker_can_never_execute()
    test_zero_capital_can_never_execute()
    test_expired_permit_can_never_execute()
    test_transaction_validator_can_never_approve_without_solders()
    print("\nALL PROPERTY/INVARIANT TESTS PASSED")


if __name__ == "__main__":
    main()
