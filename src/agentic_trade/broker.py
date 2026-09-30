"""Paper broker: a simulated cash + holdings account that fills market orders at the quote."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .config import RiskLimits
from .market_data import Quote


class OrderRejected(Exception):
    pass


@dataclass
class Fill:
    timestamp: str
    symbol: str
    side: str
    quantity: float
    price: float
    notional: float
    fee: float
    reason: str = ""


@dataclass
class Portfolio:
    cash: float
    holdings: dict[str, float] = field(default_factory=dict)
    fills: list[Fill] = field(default_factory=list)

    def equity(self, quotes: dict[str, Quote]) -> float:
        # Mark holdings at the bid: what we'd actually get selling now.
        return self.cash + sum(qty * quotes[s].bid for s, qty in self.holdings.items() if qty and s in quotes)

    def snapshot(self, quotes: dict[str, Quote]) -> dict:
        positions = []
        for s, qty in sorted(self.holdings.items()):
            if not qty:
                continue
            value = qty * quotes[s].bid if s in quotes else None
            positions.append({"symbol": s, "quantity": qty, "market_value": None if value is None else round(value, 2)})
        return {
            "cash": round(self.cash, 2),
            "equity": round(self.equity(quotes), 2),
            "positions": positions,
            "recent_fills": [asdict(f) for f in self.fills[-10:]],
        }

    @classmethod
    def load(cls, path: Path, starting_cash: float) -> "Portfolio":
        if not path.exists():
            return cls(cash=starting_cash)
        data = json.loads(path.read_text())
        return cls(cash=data["cash"], holdings=data["holdings"], fills=[Fill(**f) for f in data["fills"]])

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"cash": self.cash, "holdings": self.holdings,
                                   "fills": [asdict(f) for f in self.fills]}, indent=2))
        tmp.replace(path)


class PaperBroker:
    """Validates orders against risk limits and fills them against the portfolio.

    Orders are sized in USD notional. Buys fill at the ask, sells at the bid.
    """

    def __init__(self, portfolio: Portfolio, risk: RiskLimits, allowed_symbols: tuple[str, ...], fee_rate: float = 0.0):
        self.portfolio = portfolio
        self.risk = risk
        self.allowed_symbols = allowed_symbols
        self.fee_rate = fee_rate
        self.orders_this_run = 0

    def place_market_order(self, symbol: str, side: str, notional_usd: float, quotes: dict[str, Quote],
                           reason: str = "") -> Fill:
        symbol = symbol.upper()
        side = side.lower()
        p, r = self.portfolio, self.risk

        if symbol not in self.allowed_symbols:
            raise OrderRejected(f"{symbol} is not in the allowed symbol list {list(self.allowed_symbols)}")
        if side not in ("buy", "sell"):
            raise OrderRejected("side must be 'buy' or 'sell'")
        if not notional_usd or notional_usd <= 0:
            raise OrderRejected("notional_usd must be positive")
        if self.orders_this_run >= r.max_orders_per_run:
            raise OrderRejected(f"order limit reached ({r.max_orders_per_run} per run)")
        if notional_usd > r.max_order_notional:
            raise OrderRejected(f"order ${notional_usd:,.2f} exceeds max order size ${r.max_order_notional:,.2f}")
        if symbol not in quotes:
            raise OrderRejected(f"no quote available for {symbol}")

        quote = quotes[symbol]
        equity = p.equity(quotes)
        fee = notional_usd * self.fee_rate

        if side == "buy":
            price = quote.ask
            qty = notional_usd / price
            cost = notional_usd + fee
            if cost > p.cash:
                raise OrderRejected(f"insufficient cash: need ${cost:,.2f}, have ${p.cash:,.2f}")
            if p.cash - cost < equity * r.min_cash_reserve_pct:
                raise OrderRejected(f"order would breach the {r.min_cash_reserve_pct:.0%} cash reserve")
            position_after = (p.holdings.get(symbol, 0.0) + qty) * quote.bid
            if position_after > equity * r.max_position_pct:
                raise OrderRejected(
                    f"{symbol} would be {position_after / equity:.0%} of equity, above the {r.max_position_pct:.0%} cap")
            p.cash -= cost
            p.holdings[symbol] = p.holdings.get(symbol, 0.0) + qty
        else:
            price = quote.bid
            held = p.holdings.get(symbol, 0.0)
            qty = notional_usd / price
            # Allow a small overshoot so "sell my whole position" works when the model rounds up.
            if qty > held * 1.005:
                raise OrderRejected(f"cannot sell {qty:.8f} {symbol}; only {held:.8f} held (no shorting)")
            qty = min(qty, held)
            p.cash += qty * price - fee
            remaining = held - qty
            if remaining * price < 0.01:
                p.holdings.pop(symbol, None)
            else:
                p.holdings[symbol] = remaining

        fill = Fill(
            timestamp=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            symbol=symbol, side=side, quantity=qty, price=price,
            notional=round(qty * price, 2), fee=round(fee, 2), reason=reason,
        )
        p.fills.append(fill)
        self.orders_this_run += 1
        return fill
