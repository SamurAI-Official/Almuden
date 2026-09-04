"""Solana JSON-RPC client (aiohttp).

Read-only primitives: health, slot, version, balance, blockhash.
Latency is tracked for the circuit breaker. No signing happens here.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

import aiohttp

log = logging.getLogger(__name__)


class SolanaRpcError(Exception):
    pass


class SolanaRpcClient:
    """Async JSON-RPC 2.0 client for a Solana node."""

    def __init__(self, rpc_url: str, commitment: str = "confirmed") -> None:
        if not rpc_url:
            raise ValueError("Solana RPC URL is empty - Solana layer disabled")
        self._rpc_url = rpc_url
        self._commitment = commitment
        self._latencies: List[float] = []
        self._error_timestamps: List[float] = []

    # ── Low-level call ───────────────────────────────────────────────
    async def call(self, method: str, params: Optional[List[Any]] = None) -> Any:
        """Single JSON-RPC call. Raises SolanaRpcError on transport or RPC error."""
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params or [],
        }
        start = time.monotonic()
        try:
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(self._rpc_url, json=payload) as resp:
                    body = await resp.json()
        except Exception as exc:  # noqa: BLE001
            self._error_timestamps.append(time.time())
            raise SolanaRpcError(f"RPC {method} transport error: {exc}") from exc
        elapsed_ms = (time.monotonic() - start) * 1000.0
        self._latencies.append(elapsed_ms)
        self._latencies = self._latencies[-100:]
        if isinstance(body, dict) and body.get("error"):
            self._error_timestamps.append(time.time())
            self._error_timestamps = [
                t for t in self._error_timestamps if time.time() - t < 60
            ]
            raise SolanaRpcError(f"RPC {method} error: {body['error']}")
        return body.get("result") if isinstance(body, dict) else None

    # ── Convenience methods ──────────────────────────────────────────
    async def get_health(self) -> str:
        return await self.call("getHealth")

    async def get_slot(self) -> int:
        return int(await self.call("getSlot", [{"commitment": self._commitment}]))

    async def get_version(self) -> Dict[str, Any]:
        return await self.call("getVersion") or {}

    async def get_balance_sol(self, pubkey: str) -> float:
        result = await self.call(
            "getBalance", [pubkey, {"commitment": self._commitment}]
        )
        lamports = (result or {}).get("value", 0)
        return lamports / 1_000_000_000.0

    async def get_latest_blockhash(self) -> Dict[str, Any]:
        return await self.call(
            "getLatestBlockhash", [{"commitment": self._commitment}]
        ) or {}

    # ── Health snapshot ──────────────────────────────────────────────
    def latency_p50_ms(self) -> float:
        if not self._latencies:
            return 0.0
        ordered = sorted(self._latencies)
        return ordered[len(ordered) // 2]

    def errors_last_minute(self) -> int:
        cutoff = time.time() - 60
        return sum(1 for t in self._error_timestamps if t > cutoff)

    async def health(self) -> Dict[str, Any]:
        """Health snapshot used by the circuit breaker."""
        snapshot: Dict[str, Any] = {
            "ok": False,
            "latency_p50_ms": round(self.latency_p50_ms(), 1),
            "errors_last_minute": self.errors_last_minute(),
        }
        try:
            snapshot["health_status"] = await self.get_health()
            snapshot["slot"] = await self.get_slot()
            version = await self.get_version()
            snapshot["solana_core"] = version.get("solana-core")
            snapshot["ok"] = snapshot["health_status"] == "ok"
        except SolanaRpcError as exc:
            snapshot["error"] = str(exc)
        return snapshot

    async def close(self) -> None:
        pass
