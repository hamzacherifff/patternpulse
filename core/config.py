"""Central, dotenv-backed app configuration, grouped by domain."""

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    return float(raw) if raw else default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    return int(raw) if raw else default


def _env_list(name: str, default: list) -> list:
    raw = os.getenv(name)
    if not raw:
        return default
    return [x.strip() for x in raw.split(",") if x.strip()]


@dataclass(frozen=True)
class AppConfig:
    db_path: str = os.getenv("DB_PATH", "data_store/crypto_signal.db")
    log_dir: str = os.getenv("LOG_DIR", "logs")
    quote_asset: str = os.getenv("QUOTE_ASSET", "USDT")
    watchlist_size: int = field(default_factory=lambda: _env_int("WATCHLIST_SIZE", 15))
    timeframes: list = field(default_factory=lambda: _env_list("TIMEFRAMES", ["5m", "15m", "1h", "4h", "1d", "1w"]))


@dataclass(frozen=True)
class RiskConfig:
    default_portfolio_size: float = field(default_factory=lambda: _env_float("DEFAULT_PORTFOLIO_SIZE", 1000.0))
    default_max_risk_pct: float = field(default_factory=lambda: _env_float("DEFAULT_MAX_RISK_PCT", 1.0))
    atr_stop_multiple: float = field(default_factory=lambda: _env_float("ATR_STOP_MULTIPLE", 1.5))


@dataclass(frozen=True)
class LearningConfig:
    ml_training_threshold: int = field(default_factory=lambda: _env_int("ML_TRAINING_THRESHOLD", 300))
    outcome_lookforward_candles: int = field(default_factory=lambda: _env_int("OUTCOME_LOOKFORWARD_CANDLES", 30))


@dataclass(frozen=True)
class TelegramConfig:
    bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")


app = AppConfig()
risk = RiskConfig()
learning = LearningConfig()
telegram = TelegramConfig()
