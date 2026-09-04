"""Portfolio accounting — marks all positions to a common numeraire.

The old risk engine summed raw token quantities across venues (e.g.
4 SOL + 700 XMR + 18,000 USDC = "18,704 exposure"), which is meaningless
because the units differ. Everything is converted to a single numeraire
(USDC/USD) before being aggregated.

Exposure views:
  - gross exposure      : Σ |qty * mark| over all assets
  - net exposure        : Σ qty * mark (longs minus shorts)
  - asset exposure      : exposure per asset symbol
  - venue exposure      : exposure per venue (counterparty)
  - strategy exposure   : exposure attributed to each strategy
  - chain exposure      : exposure per chain (e.g. "cex", "solana")
  - stablecoin exposure : share of portfolio held in stablecoins
"""
from __future__ import annotations

import logging
from typing import Dict, Optional

log = logging.getLogger(__name__)

# Common numeraire. Everything is marked in this asset.
NUMERAIRE = "USDT"

# Recognised stablecoins (for stablecoin exposure reporting).
STABLECOINS = {"USDT", "USDC", "DAI", "BUSD", "TUSD", "FDUSD"}

# Chain label for CEX venues vs on-chain venues.
CEX_CHAINS = {"kucoin", "gateio", "mexc", "kraken", "whitebit"}


class Portfolio:
    """Marked-to-USD view of balances across venues."""

    def __init__(
        self,
        balances: Optional[Dict[str, Dict[str, float]]] = None,
        mark_prices: Optional[Dict[str, float]] = None,
        strategy_tags: Optional[Dict[str, str]] = None,
    ) -> None:
        # balances: venue -> asset -> quantity
        self._balances: Dict[str, Dict[str, float]] = balances or {}
        # mark_prices: asset -> price in numeraire (USDT). USDT -> 1.0.
        self._mark_prices: Dict[str, float] = dict(mark_prices or {})
        self._mark_prices.setdefault(NUMERAIRE, 1.0)
        # strategy_tags: (venue, asset) -> strategy_id for strategy exposure
        self._strategy_tags: Dict[str, str] = strategy_tags or {}

    # ── Marking helpers ─────────────────────────────────────────────

    def _mark(self, asset: str) -> float:
        """Return the mark price of *asset* in numeraire; unknown -> small penalty."""
        if asset == NUMERAIRE:
            return 1.0
        price = self._mark_prices.get(asset)
        if price is None:
            # Unknown assets are marked at 0 so they do not inflate exposure.
            return 0.0
        return float(price)

    def set_mark_prices(self, mark_prices: Dict[str, float]) -> None:
        self._mark_prices.update(mark_prices)
        self._mark_prices.setdefault(NUMERAIRE, 1.0)

    def asset_value(self, venue: str, asset: str) -> float:
        """USD value of a single asset position."""
        qty = self._balances.get(venue, {}).get(asset, 0.0)
        return qty * self._mark(asset)

    # ── Aggregate views ─────────────────────────────────────────────

    @property
    def total_value(self) -> float:
        """Total portfolio value (net) in numeraire."""
        return sum(
            qty * self._mark(asset)
            for venue_assets in self._balances.values()
            for asset, qty in venue_assets.items()
        )

    @property
    def gross_exposure(self) -> float:
        """Σ |qty * mark| — ignores long/short offsetting."""
        return sum(
            abs(qty * self._mark(asset))
            for venue_assets in self._balances.values()
            for asset, qty in venue_assets.items()
        )

    @property
    def net_exposure(self) -> float:
        """Σ qty * mark — longs offset shorts."""
        return self.total_value

    def asset_exposure(self) -> Dict[str, float]:
        """Exposure per asset symbol across all venues."""
        out: Dict[str, float] = {}
        for venue_assets in self._balances.values():
            for asset, qty in venue_assets.items():
                out[asset] = out.get(asset, 0.0) + qty * self._mark(asset)
        return out

    def venue_exposure(self) -> Dict[str, float]:
        """Exposure per venue in numeraire (counterparty view)."""
        return {
            venue: sum(
                qty * self._mark(asset)
                for asset, qty in venue_assets.items()
            )
            for venue, venue_assets in self._balances.items()
        }

    def chain_exposure(self, chain_map: Optional[Dict[str, str]] = None) -> Dict[str, float]:
        """Exposure per chain, e.g. 'cex' vs 'solana'."""
        cmap = chain_map or {
            v: "cex" for v in CEX_CHAINS
        }
        out: Dict[str, float] = {}
        venue_exposure = self.venue_exposure()
        for venue, value in venue_exposure.items():
            chain = cmap.get(venue, "other")
            out[chain] = out.get(chain, 0.0) + value
        return out

    def stablecoin_exposure(self) -> float:
        """Value held in stablecoins."""
        return sum(
            qty * self._mark(asset)
            for venue_assets in self._balances.values()
            for asset, qty in venue_assets.items()
            if asset in STABLECOINS
        )

    def strategy_exposure(self) -> Dict[str, float]:
        """Exposure attributed per strategy via (venue, asset) tags."""
        out: Dict[str, float] = {}
        for venue_assets in self._balances.values():
            for asset, qty in venue_assets.items():
                value = qty * self._mark(asset)
                out["unallocated"] = out.get("unallocated", 0.0) + value
        for key, strategy in self._strategy_tags.items():
            # key format "venue:asset"
            venue, _, asset = key.partition(":")
            value = self.asset_value(venue, asset)
            out[strategy] = out.get(strategy, 0.0) + value
            out["unallocated"] = out.get("unallocated", 0.0) - value
        return out

    def summary(self) -> Dict[str, object]:
        """Serializable summary for logs and the API."""
        return {
            "total_value": round(self.total_value, 4),
            "gross_exposure": round(self.gross_exposure, 4),
            "net_exposure": round(self.net_exposure, 4),
            "stablecoin_exposure": round(self.stablecoin_exposure(), 4),
            "asset_exposure": {
                k: round(v, 4) for k, v in sorted(self.asset_exposure().items())
            },
            "venue_exposure": {
                k: round(v, 4) for k, v in sorted(self.venue_exposure().items())
            },
            "chain_exposure": {
                k: round(v, 4) for k, v in sorted(self.chain_exposure().items())
            },
        }