"""Binance public market data: historical klines (REST) and live klines (WebSocket).

Uses python-binance, a purpose-built SDK that only ever talks to binance.com, so it is
a deliberate exception to core.security.safe_request()'s host-allowlist wrapper (see
core/security.py docstring). No API key is used -- these are all public endpoints.
"""

import logging
from datetime import datetime, timezone

from binance.client import Client
from binance import ThreadedWebsocketManager

from core.security import safe_request

logger = logging.getLogger(__name__)

_INTERVAL_MAP = {
    "1m": Client.KLINE_INTERVAL_1MINUTE,
    "5m": Client.KLINE_INTERVAL_5MINUTE,
    "15m": Client.KLINE_INTERVAL_15MINUTE,
    "30m": Client.KLINE_INTERVAL_30MINUTE,
    "1h": Client.KLINE_INTERVAL_1HOUR,
    "4h": Client.KLINE_INTERVAL_4HOUR,
    "1d": Client.KLINE_INTERVAL_1DAY,
    "1w": Client.KLINE_INTERVAL_1WEEK,
}

_client = None


def get_client() -> Client:
    global _client
    if _client is None:
        _client = Client(api_key=None, api_secret=None)
    return _client


def get_ticker_price(symbol: str) -> float:
    """A single lightweight price lookup -- roughly an order of magnitude cheaper than
    fetching a full kline history, used to make the current candle visibly tick in
    real time without re-fetching and re-processing the whole chart every second."""
    return float(get_client().get_symbol_ticker(symbol=symbol)["price"])


def fetch_klines(symbol: str, timeframe: str, limit: int = 500) -> list[dict]:
    """Returns oldest-to-newest OHLCV candles for `symbol` on `timeframe`."""
    interval = _INTERVAL_MAP.get(timeframe)
    if interval is None:
        raise ValueError(f"Unsupported timeframe: {timeframe}")
    raw = get_client().get_klines(symbol=symbol, interval=interval, limit=limit)
    candles = []
    for row in raw:
        candles.append(
            {
                "symbol": symbol,
                "timeframe": timeframe,
                "ts": int(row[0]),
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
                "volume": float(row[5]),
            }
        )
    return candles


def get_tradable_symbols(quote_asset: str) -> set[str]:
    info = get_client().get_exchange_info()
    return {
        s["symbol"]
        for s in info["symbols"]
        if s["quoteAsset"] == quote_asset and s["status"] == "TRADING"
    }


# Stablecoins have no meaningful chart patterns (they're designed not to move) so they're
# excluded even when they rank highly by market cap.
_STABLECOIN_SYMBOLS = {
    "USDT", "USDC", "USDS", "DAI", "TUSD", "FDUSD", "USDP", "USDD", "PYUSD", "GUSD", "EURS", "USDE",
}


def fetch_top_symbols_by_market_cap(n: int, quote_asset: str = "USDT") -> list[str]:
    """Top-N non-stablecoin coins by market cap (via CoinGecko) that also have a tradable spot pair on Binance."""
    resp = safe_request(
        "GET",
        "https://api.coingecko.com/api/v3/coins/markets",
        params={"vs_currency": "usd", "order": "market_cap_desc", "per_page": n * 3, "page": 1},
    )
    resp.raise_for_status()
    ranked = resp.json()

    tradable = get_tradable_symbols(quote_asset)
    result = []
    for coin in ranked:
        base = coin["symbol"].upper()
        if base in _STABLECOIN_SYMBOLS:
            continue
        candidate = base + quote_asset
        if candidate in tradable and candidate not in result:
            result.append(candidate)
        if len(result) >= n:
            break
    return result


class LiveKlineStream:
    """Wraps ThreadedWebsocketManager to stream closed klines for (symbol, timeframe) pairs."""

    def __init__(self):
        self._twm = ThreadedWebsocketManager()
        self._started = False

    def start(self):
        if not self._started:
            self._twm.start()
            self._started = True

    def subscribe(self, symbol: str, timeframe: str, on_closed_candle) -> str:
        interval = _INTERVAL_MAP.get(timeframe)
        if interval is None:
            raise ValueError(f"Unsupported timeframe: {timeframe}")

        def _handle(msg):
            k = msg.get("k", {})
            if not k.get("x"):  # only emit on candle close
                return
            on_closed_candle(
                {
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "ts": int(k["t"]),
                    "open": float(k["o"]),
                    "high": float(k["h"]),
                    "low": float(k["l"]),
                    "close": float(k["c"]),
                    "volume": float(k["v"]),
                }
            )

        return self._twm.start_kline_socket(callback=_handle, symbol=symbol, interval=interval)

    def stop(self):
        if self._started:
            self._twm.stop()
            self._started = False


def to_iso(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).isoformat()
