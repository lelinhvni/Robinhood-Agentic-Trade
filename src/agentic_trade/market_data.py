"""Market data sources.

`RobinhoodCryptoMarketData` reads live best bid/ask quotes from the official
Robinhood Crypto Trading API. It only calls read-only market data endpoints;
this project never sends orders to Robinhood.

`SimulatedMarketData` is a seeded random walk for offline runs and tests.
"""

from __future__ import annotations

import base64
import json
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import urlencode

import requests
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


@dataclass(frozen=True)
class Quote:
    symbol: str
    bid: float
    ask: float

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2

    def to_dict(self) -> dict:
        return {"symbol": self.symbol, "bid": self.bid, "ask": self.ask, "mid": round(self.mid, 8)}


class MarketData(Protocol):
    def quotes(self, symbols: list[str]) -> dict[str, Quote]: ...


class RobinhoodCryptoMarketData:
    """Signed client for Robinhood's Crypto Trading API (market data only)."""

    BASE_URL = "https://trading.robinhood.com"

    def __init__(self, api_key: str, private_key_base64: str, session: requests.Session | None = None):
        seed = base64.b64decode(private_key_base64)
        # Robinhood's key generator prints the 32-byte seed; accept a 64-byte seed||public key too.
        self._key = Ed25519PrivateKey.from_private_bytes(seed[:32])
        self._api_key = api_key
        self._session = session or requests.Session()

    def _headers(self, method: str, path: str, body: str = "") -> dict[str, str]:
        timestamp = str(int(time.time()))
        message = f"{self._api_key}{timestamp}{path}{method}{body}"
        signature = base64.b64encode(self._key.sign(message.encode())).decode()
        return {
            "x-api-key": self._api_key,
            "x-signature": signature,
            "x-timestamp": timestamp,
            "Content-Type": "application/json; charset=utf-8",
        }

    def _get(self, path: str) -> dict:
        resp = self._session.get(self.BASE_URL + path, headers=self._headers("GET", path), timeout=10)
        resp.raise_for_status()
        return resp.json()

    def quotes(self, symbols: list[str]) -> dict[str, Quote]:
        query = urlencode([("symbol", s) for s in symbols])
        data = self._get(f"/api/v1/crypto/marketdata/best_bid_ask/?{query}")
        out: dict[str, Quote] = {}
        for row in data.get("results", []):
            out[row["symbol"]] = Quote(
                symbol=row["symbol"],
                bid=float(row["bid_inclusive_of_sell_spread"]),
                ask=float(row["ask_inclusive_of_buy_spread"]),
            )
        missing = set(symbols) - out.keys()
        if missing:
            raise LookupError(f"No quote returned for: {', '.join(sorted(missing))}")
        return out


class SimulatedMarketData:
    """Random-walk prices with a fixed spread. Each `quotes` call advances one step."""

    DEFAULT_PRICES = {"BTC-USD": 65_000.0, "ETH-USD": 3_200.0, "SOL-USD": 150.0, "DOGE-USD": 0.15}

    def __init__(self, seed: int | None = None, volatility: float = 0.01, spread: float = 0.002,
                 prices: dict[str, float] | None = None):
        self._rng = random.Random(seed)
        self._vol = volatility
        self._spread = spread
        self._prices = dict(prices or self.DEFAULT_PRICES)

    def quotes(self, symbols: list[str]) -> dict[str, Quote]:
        out = {}
        for s in symbols:
            price = self._prices.setdefault(s, 100.0)
            price *= 1 + self._rng.gauss(0, self._vol)
            self._prices[s] = price
            half = price * self._spread / 2
            out[s] = Quote(symbol=s, bid=round(price - half, 8), ask=round(price + half, 8))
        return out


class PriceHistory:
    """Mid prices recorded once per agent run, persisted between runs."""

    def __init__(self, path: Path, max_points: int = 200):
        self.path = Path(path)
        self.max_points = max_points
        self.data: dict[str, list[list]] = json.loads(self.path.read_text()) if self.path.exists() else {}

    def record(self, quotes: dict[str, Quote], timestamp: str) -> None:
        for s, q in quotes.items():
            series = self.data.setdefault(s, [])
            series.append([timestamp, round(q.mid, 8)])
            del series[:-self.max_points]

    def get(self, symbol: str, limit: int = 50) -> list[list]:
        return self.data.get(symbol, [])[-limit:]

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data))


def from_env(env: dict[str, str], seed: int | None = None) -> MarketData:
    key, secret = env.get("ROBINHOOD_API_KEY"), env.get("ROBINHOOD_PRIVATE_KEY_BASE64")
    if key and secret:
        return RobinhoodCryptoMarketData(key, secret)
    return SimulatedMarketData(seed=seed)


def dumps_quotes(quotes: dict[str, Quote]) -> str:
    return json.dumps([q.to_dict() for q in quotes.values()])
