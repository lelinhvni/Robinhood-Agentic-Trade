"""Market data sources.

Live quotes come from `robinhood.RobinhoodCryptoClient`, which also satisfies
the `MarketData` protocol.

`SimulatedMarketData` is a seeded random walk for offline runs and tests.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


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
        from .robinhood import RobinhoodCryptoClient
        return RobinhoodCryptoClient(key, secret)
    return SimulatedMarketData(seed=seed)
