"""Price fetching from the Binance public API (no API key required)."""

import httpx

BINANCE_URL = "https://api.binance.com/api/v3/ticker/price"
BINANCE_KLINES = "https://api.binance.com/api/v3/klines"

# Quote assets we recognise when a user types a full pair like "ETHBTC".
QUOTES = ("USDT", "USDC", "FDUSD", "BUSD", "TRY")


class PriceError(Exception):
    """Raised when a symbol is unknown or invalid."""


def normalize_symbol(raw: str) -> str:
    """Turn user input into a Binance symbol.

    "btc" -> "BTCUSDT", "eth/usdt" -> "ETHUSDT", "ethbtc" -> "ETHBTC".
    Bare tickers default to a USDT pair.
    """
    s = raw.strip().upper().replace("/", "").replace("-", "")
    if any(s.endswith(q) for q in QUOTES) and len(s) > 4:
        return s
    if s.endswith(("BTC", "ETH")) and len(s) >= 6:  # cross pair, e.g. ETHBTC
        return s
    return s + "USDT"


async def get_price(symbol: str) -> float:
    """Return the last price for a Binance symbol."""
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(BINANCE_URL, params={"symbol": symbol})
    if resp.status_code == 400:
        raise PriceError(f"Unknown symbol: {symbol}")
    resp.raise_for_status()
    return float(resp.json()["price"])


async def get_klines(symbol: str, interval: str = "1h", limit: int = 24) -> list[dict]:
    """Return recent OHLC candles for a Binance symbol.

    Each candle is a dict with time (ms), open, high, low, close.
    """
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            BINANCE_KLINES,
            params={"symbol": symbol, "interval": interval, "limit": limit},
        )
    if resp.status_code == 400:
        raise PriceError(f"Unknown symbol: {symbol}")
    resp.raise_for_status()
    return [
        {
            "time": k[0],
            "open": float(k[1]),
            "high": float(k[2]),
            "low": float(k[3]),
            "close": float(k[4]),
        }
        for k in resp.json()
    ]


def fmt(value: float) -> str:
    """Human-friendly price formatting."""
    if value >= 1:
        return f"{value:,.2f}"
    return f"{value:.6f}".rstrip("0").rstrip(".")
