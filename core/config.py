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
    # Real production data (1293 resolved signals) showed 95% of signals had a target closer
    # than the stop (median reward:risk = 0.37:1) -- the stop is deliberately set wide (never
    # inside a level that would invalidate the pattern) but the target was never recalibrated
    # against that width, so even a 68% win rate produced a NEGATIVE average return. A
    # professional trader simply skips a setup whose own geometry doesn't clear a sane
    # reward:risk bar, rather than taking it and hoping the win rate carries it -- this is that
    # discipline, enforced at candidate-selection time in signals/engine.py.
    min_reward_risk_ratio: float = field(default_factory=lambda: _env_float("MIN_REWARD_RISK_RATIO", 1.2))


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
