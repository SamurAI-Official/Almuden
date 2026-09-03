"""Configuration loader.

Reads ``.env`` (if present) then the process environment. All values have
sensible defaults so the system runs with zero configuration in paper mode.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List


def _get(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _getfloat(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        raise SystemExit(f"Invalid float for {name}: {raw!r}")


def _getint(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        raise SystemExit(f"Invalid int for {name}: {raw!r}")


def _getbool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _getlist(name: str, default: List[str]) -> List[str]:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return list(default)
    return [item.strip() for item in raw.split(",") if item.strip()]


def load_dotenv(path: str = ".env") -> None:
    """Load KEY=VALUE pairs from *path* without clobbering real env vars."""
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


@dataclass
class ExchangeKeys:
    """Per-exchange API credentials. All empty in paper mode."""

    kucoin_key: str = ""
    kucoin_secret: str = ""
    kucoin_passphrase: str = ""
    gateio_key: str = ""
    gateio_secret: str = ""
    mexc_key: str = ""
    mexc_secret: str = ""
    kraken_key: str = ""
    kraken_secret: str = ""
    whitebit_key: str = ""
    whitebit_secret: str = ""


@dataclass
class Settings:
    """All runtime settings, populated from env / .env."""

    # ── Mode ────────────────────────────────────────────────────────
    mode: str = "paper"  # paper | live

    # ── Venues & symbols ────────────────────────────────────────────
    venues: List[str] = field(
        default_factory=lambda: [
            "kucoin",
            "gateio",
            "mexc",
            "kraken",
            "whitebit",
        ]
    )

    # ── Arbitrage parameters ─────────────────────────────────────────
    min_edge_bps: float = 10.0
    max_position: float = 100.0
    max_drift_bps: float = 500.0

    # ── Live trading kill switch ─────────────────────────────────────
    live_enabled: bool = False
    live_kill_switch: bool = False

    # ── Infrastructure ───────────────────────────────────────────────
    redis_url: str = ""
    postgres_dsn: str = ""
    log_level: str = "INFO"

    # ── Exchange keys ────────────────────────────────────────────────
    keys: ExchangeKeys = field(default_factory=ExchangeKeys)


def load_settings(dotenv_path: str = ".env") -> Settings:
    """Load settings from *dotenv_path* then the environment."""
    load_dotenv(dotenv_path)

    mode = _get("ALMUDEN_MODE", "paper").lower()
    if mode not in ("paper", "live"):
        raise SystemExit(f"ALMUDEN_MODE must be 'paper' or 'live', got {mode!r}")

    live_enabled = _getbool("ALMUDEN_LIVE_ENABLED", False)
    live_kill_switch = _getbool("ALMUDEN_LIVE_KILL_SWITCH", False)

    # Live mode requires explicit opt-in on both flags.
    if mode == "live" and not live_enabled:
        raise SystemExit(
            "ALMUDEN_MODE=live but ALMUDEN_LIVE_ENABLED is not 'true'. "
            "Set ALMUDEN_LIVE_ENABLED=true to enable live trading."
        )

    keys = ExchangeKeys(
        kucoin_key=_get("KUCOIN_API_KEY", ""),
        kucoin_secret=_get("KUCOIN_SECRET", ""),
        kucoin_passphrase=_get("KUCOIN_PASSPHRASE", ""),
        gateio_key=_get("GATEIO_API_KEY", ""),
        gateio_secret=_get("GATEIO_SECRET", ""),
        mexc_key=_get("MEXC_API_KEY", ""),
        mexc_secret=_get("MEXC_SECRET", ""),
        kraken_key=_get("KRAKEN_API_KEY", ""),
        kraken_secret=_get("KRAKEN_SECRET", ""),
        whitebit_key=_get("WHITEBIT_API_KEY", ""),
        whitebit_secret=_get("WHITEBIT_SECRET", ""),
    )

    return Settings(
        mode=mode,
        venues=_getlist(
            "ALMUDEN_VENUES",
            ["kucoin", "gateio", "mexc", "kraken", "whitebit"],
        ),
        min_edge_bps=_getfloat("ALMUDEN_MIN_EDGE_BPS", 10.0),
        max_position=_getfloat("ALMUDEN_MAX_POSITION", 100.0),
        max_drift_bps=_getfloat("ALMUDEN_MAX_DRIFT_BPS", 500.0),
        live_enabled=live_enabled,
        live_kill_switch=live_kill_switch,
        redis_url=_get("ALMUDEN_REDIS_URL", ""),
        postgres_dsn=_get("ALMUDEN_POSTGRES_DSN", ""),
        log_level=_get("ALMUDEN_LOG_LEVEL", "INFO").upper(),
        keys=keys,
    )
