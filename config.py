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
    default_quote: str = "USDT"  # Default quote currency for seeding balances


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

    # ── Triangular arbitrage ────────────────────────────────────────
    triangular_enabled: bool = True
    triangular_symbols: List[str] = field(
        default_factory=lambda: ["XMR/ERG", "ERG/XMR"]
    )

    # ── Environment / market intelligence ────────────────────────────
    news_poll_interval: float = 60.0  # Seconds between news polls
    regime_lookback: int = 20  # Observations for regime detection
    sentiment_threshold: float = 0.3  # Minimum absolute sentiment to report

    # ── Strategy lab / backtesting ──────────────────────────────────
    backtest_cache_dir: str = ".cache"  # Directory for cached OHLCV data
    monte_carlo_draws: int = 1000  # Default number of simulation runs

    # ── Agent system ────────────────────────────────────────────────
    ollama_url: str = "http://localhost:11434"  # Ollama server URL
    ollama_model: str = "llama3"  # Default Ollama model
    memory_dir: str = ".memory"  # Directory for persistent memory
    short_term_capacity: int = 50  # Number of observations in short-term memory

    # ── API server ──────────────────────────────────────────────────
    api_host: str = "0.0.0.0"  # API server host
    api_port: int = 8080  # API server port
    api_key: str = ""  # API key (auto-generated if empty)

    # ── Live trading kill switch ─────────────────────────────────────
    live_enabled: bool = False
    live_kill_switch: bool = False

    # ── Risk engine ─────────────────────────────────────────────────
    max_drawdown_pct: float = 10.0  # Max drawdown before stopping
    max_daily_loss: float = 100.0  # Max daily loss in quote currency
    max_open_orders: int = 5  # Max concurrent open orders
    max_consecutive_losses: int = 5  # Circuit breaker threshold
    max_venue_exposure: float = 500.0  # Max exposure per venue
    max_errors_per_minute: int = 10  # Max API errors per minute

    # ── Capital scheduler ───────────────────────────────────────────
    capital_deploy_fraction: float = 0.05  # Start with 5% of capital

    # ── Alerting ────────────────────────────────────────────────────
    alert_webhook_url: str = ""  # Discord webhook URL
    telegram_bot_token: str = ""  # Telegram bot token
    telegram_chat_id: str = ""  # Telegram chat ID

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
        triangular_enabled=_getbool("ALMUDEN_TRIANGULAR_ENABLED", True),
        triangular_symbols=_getlist(
            "ALMUDEN_TRIANGULAR_SYMBOLS", ["XMR/ERG", "ERG/XMR"]
        ),
        news_poll_interval=_getfloat("ALMUDEN_NEWS_POLL_INTERVAL", 60.0),
        regime_lookback=_getint("ALMUDEN_REGIME_LOOKBACK", 20),
        sentiment_threshold=_getfloat("ALMUDEN_SENTIMENT_THRESHOLD", 0.3),
        backtest_cache_dir=_get("ALMUDEN_BACKTEST_CACHE_DIR", ".cache"),
        monte_carlo_draws=_getint("ALMUDEN_MONTE_CARLO_DRAWS", 1000),
        ollama_url=_get("ALMUDEN_OLLAMA_URL", "http://localhost:11434"),
        ollama_model=_get("ALMUDEN_OLLAMA_MODEL", "llama3"),
        memory_dir=_get("ALMUDEN_MEMORY_DIR", ".memory"),
        short_term_capacity=_getint("ALMUDEN_SHORT_TERM_CAPACITY", 50),
        api_host=_get("ALMUDEN_API_HOST", "0.0.0.0"),
        api_port=_getint("ALMUDEN_API_PORT", 8080),
        api_key=_get("ALMUDEN_API_KEY", ""),
        max_drawdown_pct=_getfloat("ALMUDEN_MAX_DRAWDOWN_PCT", 10.0),
        max_daily_loss=_getfloat("ALMUDEN_MAX_DAILY_LOSS", 100.0),
        max_open_orders=_getint("ALMUDEN_MAX_OPEN_ORDERS", 5),
        max_consecutive_losses=_getint("ALMUDEN_MAX_CONSECUTIVE_LOSSES", 5),
        max_venue_exposure=_getfloat("ALMUDEN_MAX_VENUE_EXPOSURE", 500.0),
        max_errors_per_minute=_getint("ALMUDEN_MAX_ERRORS_PER_MINUTE", 10),
        capital_deploy_fraction=_getfloat("ALMUDEN_CAPITAL_DEPLOY_FRACTION", 0.05),
        alert_webhook_url=_get("ALMUDEN_ALERT_WEBHOOK_URL", ""),
        telegram_bot_token=_get("ALMUDEN_TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=_get("ALMUDEN_TELEGRAM_CHAT_ID", ""),
        live_enabled=live_enabled,
        live_kill_switch=live_kill_switch,
        redis_url=_get("ALMUDEN_REDIS_URL", ""),
        postgres_dsn=_get("ALMUDEN_POSTGRES_DSN", ""),
        log_level=_get("ALMUDEN_LOG_LEVEL", "INFO").upper(),
        keys=keys,
    )
