"""Technical indicators built on the pure-Python `ta` library (no compiled TA-Lib dependency)."""

import pandas as pd
import ta


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Adds MACD, RSI, Bollinger Bands, ATR, OBV, ADX, Stochastic, VWAP, moving averages,
    volume-spike and Bollinger-squeeze columns."""
    out = df.copy()
    close, high, low, volume = out["close"], out["high"], out["low"], out["volume"]

    macd = ta.trend.MACD(close)
    out["macd"] = macd.macd()
    out["macd_signal"] = macd.macd_signal()
    out["macd_diff"] = macd.macd_diff()

    out["rsi"] = ta.momentum.RSIIndicator(close).rsi()

    bb = ta.volatility.BollingerBands(close)
    out["bb_high"] = bb.bollinger_hband()
    out["bb_mid"] = bb.bollinger_mavg()
    out["bb_low"] = bb.bollinger_lband()
    out["bb_width"] = (out["bb_high"] - out["bb_low"]) / out["bb_mid"]
    out["bb_squeeze"] = out["bb_width"] < out["bb_width"].rolling(window=50, min_periods=10).quantile(0.2)

    out["atr"] = ta.volatility.AverageTrueRange(high, low, close).average_true_range()
    out["obv"] = ta.volume.OnBalanceVolumeIndicator(close, volume).on_balance_volume()

    adx = ta.trend.ADXIndicator(high, low, close)
    out["adx"] = adx.adx()
    out["di_plus"] = adx.adx_pos()
    out["di_minus"] = adx.adx_neg()

    stoch = ta.momentum.StochasticOscillator(high, low, close)
    out["stoch_k"] = stoch.stoch()
    out["stoch_d"] = stoch.stoch_signal()

    out["vwap"] = ta.volume.VolumeWeightedAveragePrice(high, low, close, volume).volume_weighted_average_price()

    out["sma20"] = ta.trend.SMAIndicator(close, window=20).sma_indicator()
    out["sma50"] = ta.trend.SMAIndicator(close, window=50).sma_indicator()
    out["ema20"] = ta.trend.EMAIndicator(close, window=20).ema_indicator()

    out["volume_sma20"] = volume.rolling(window=20).mean()
    out["volume_spike"] = volume > out["volume_sma20"] * 1.5

    return out


def is_strong_trend(df: pd.DataFrame, i: int, threshold: float = 25.0) -> bool:
    """ADX above `threshold` conventionally signals a trending (vs. choppy/ranging) market."""
    adx = df["adx"].iloc[i]
    return bool(adx) and not pd.isna(adx) and adx >= threshold


def stoch_bullish_cross(df: pd.DataFrame, i: int) -> bool:
    if i < 1:
        return False
    return df["stoch_k"].iloc[i - 1] <= df["stoch_d"].iloc[i - 1] and df["stoch_k"].iloc[i] > df["stoch_d"].iloc[i]


def stoch_bearish_cross(df: pd.DataFrame, i: int) -> bool:
    if i < 1:
        return False
    return df["stoch_k"].iloc[i - 1] >= df["stoch_d"].iloc[i - 1] and df["stoch_k"].iloc[i] < df["stoch_d"].iloc[i]


def macd_bullish_cross(df: pd.DataFrame, i: int) -> bool:
    if i < 1:
        return False
    return df["macd_diff"].iloc[i - 1] <= 0 < df["macd_diff"].iloc[i]


def macd_bearish_cross(df: pd.DataFrame, i: int) -> bool:
    if i < 1:
        return False
    return df["macd_diff"].iloc[i - 1] >= 0 > df["macd_diff"].iloc[i]


def trend_direction(df: pd.DataFrame, i: int) -> str:
    """Simple higher-timeframe trend filter: price vs SMA50."""
    if pd.isna(df["sma50"].iloc[i]):
        return "flat"
    return "up" if df["close"].iloc[i] > df["sma50"].iloc[i] else "down"
