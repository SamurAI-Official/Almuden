"""Market data verification script.
Tests access to exchange data and validates data quality.
"""
from __future__ import annotations
import asyncio
from typing import Dict
import pandas as pd
from config import Settings

VENUES = {"kucoin": "kucoin", "gateio": "gate", "mexc": "mexc", "kraken": "kraken", "whitebit": "whitebit"}
SYMBOLS = ["ERG/USDT", "XMR/USDT", "BTC/USDT"]


async def verify_exchange(venue_id: str, ccxt_id: str) -> Dict:
    import ccxt.async_support as ccxt
    result = {"venue": venue_id, "connected": False, "symbols": {}, "ohlcv": False, "order_book": False, "error": None}
    try:
        exchange = getattr(ccxt, ccxt_id)({"enableRateLimit": True, "options": {"defaultType": "spot"}})
        await exchange.load_markets()
        result["connected"] = True
        for sym in SYMBOLS:
            result["symbols"][sym] = sym in exchange.markets
        for sym in SYMBOLS:
            if sym in exchange.markets:
                try:
                    bars = await exchange.fetch_ohlcv(sym, "1h", limit=10)
                    if bars:
                        result["ohlcv"] = True
                        result["ohlcv_bars"] = len(bars)
                        break
                except Exception as e:
                    result["ohlcv_error"] = str(e)
        for sym in SYMBOLS:
            if sym in exchange.markets:
                try:
                    limit = 100 if venue_id == "kucoin" else 20
                    ob = await exchange.fetch_order_book(sym, limit=limit)
                    if ob.get("bids") and ob.get("asks"):
                        result["order_book"] = True
                        result["book_bids"] = len(ob["bids"])
                        result["book_asks"] = len(ob["asks"])
                        break
                except Exception as e:
                    result["book_error"] = str(e)
        await exchange.close()
    except Exception as e:
        result["error"] = str(e)
    return result


async def main():
    print("=" * 60)
    print("MARKET DATA VERIFICATION")
    print("=" * 60)
    print()
    print("--- Exchange Connections ---")
    results = []
    for venue_id, ccxt_id in VENUES.items():
        result = await verify_exchange(venue_id, ccxt_id)
        results.append(result)
        status = "OK" if result["connected"] else "FAILED"
        print(f"  {venue_id}: {status}")
        if result["error"]:
            print(f"    Error: {result['error']}")
        if result["connected"]:
            for sym, avail in result["symbols"].items():
                s = "OK" if avail else "MISSING"
                print(f"    {sym}: {s}")
            ohlcv = "OK" if result["ohlcv"] else "FAILED"
            book = "OK" if result["order_book"] else "FAILED"
            print(f"    OHLCV: {ohlcv}")
            print(f"    Order Book: {book}")
    print()
    print("--- Arbitrage Pairs ---")
    for sym in SYMBOLS:
        venues_with_sym = [r["venue"] for r in results if r["symbols"].get(sym)]
        if len(venues_with_sym) >= 2:
            v = ", ".join(venues_with_sym)
            print(f"  {sym}: {v}")
    print()
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
