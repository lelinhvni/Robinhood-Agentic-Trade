"""Live broker: places real market orders on Robinhood through the Crypto Trading API.

Every order runs the same risk checks as the paper broker. It then has to pass
an approval callback before it is sent. The CLI's callback approves automatically, or asks at
the terminal with `--confirm`.
Cash and holdings always come from Robinhood. The local Portfolio only keeps the fill log.
"""

from __future__ import annotations

import logging
import uuid
from typing import Callable

from .broker import Fill, OrderRejected, Portfolio, SizedOrder, check_order, now_iso
from .config import RiskLimits
from .market_data import Quote
from .robinhood import RobinhoodAPIError, RobinhoodCryptoClient, round_to_increment

log = logging.getLogger(__name__)

Approver = Callable[[SizedOrder, str], bool]


def mask(account_number: str) -> str:
    return "••••" + str(account_number)[-4:]


class LiveBroker:
    def __init__(self, client: RobinhoodCryptoClient, portfolio: Portfolio, risk: RiskLimits,
                 allowed_symbols: tuple[str, ...], approve: Approver, expected_account: str | None = None):
        self.client = client
        self.portfolio = portfolio
        self.risk = risk
        self.allowed_symbols = allowed_symbols
        self.approve = approve
        self.expected_account = expected_account
        self.orders_this_run = 0
        self.account: dict = {}
        self._pairs: dict[str, dict] = {}

    def sync(self) -> None:
        """Refresh cash and holdings from Robinhood. Refuses to continue on an unexpected account."""
        self.account = self.client.account()
        number = str(self.account.get("account_number", ""))
        if self.expected_account and number != self.expected_account:
            raise RuntimeError(f"Robinhood API key belongs to crypto account {mask(number)}, "
                               f"not the expected {mask(self.expected_account)}. Refusing to trade.")
        if str(self.account.get("status", "")).lower() != "active":
            raise RuntimeError(f"Crypto account {mask(number)} is not active (status: {self.account.get('status')})")
        self.portfolio.cash = float(self.account["buying_power"])
        self.portfolio.holdings = self.client.holdings()

    def _pair(self, symbol: str) -> dict:
        if symbol not in self._pairs:
            self._pairs[symbol] = self.client.trading_pair(symbol)
        return self._pairs[symbol]

    def place_market_order(self, symbol: str, side: str, notional_usd: float, quotes: dict[str, Quote],
                           reason: str = "") -> Fill:
        sized = check_order(self.portfolio, self.risk, self.allowed_symbols, self.orders_this_run,
                            symbol, side, notional_usd, quotes)

        pair = self._pair(sized.symbol)
        if str(pair.get("status", "tradable")).lower() != "tradable":
            raise OrderRejected(f"{sized.symbol} is not currently tradable on Robinhood")
        quantity = round_to_increment(sized.quantity, pair["asset_increment"])
        if float(quantity) < float(pair["min_order_size"]):
            raise OrderRejected(f"${notional_usd:,.2f} is {quantity} {sized.symbol}, below Robinhood's minimum "
                                f"order size of {pair['min_order_size']}")
        if float(quantity) > float(pair["max_order_size"]):
            raise OrderRejected(f"{quantity} {sized.symbol} exceeds Robinhood's maximum order size")
        sized = SizedOrder(sized.symbol, sized.side, float(quantity), sized.price, 0.0)

        if not self.approve(sized, reason):
            raise OrderRejected("the human operator declined this order")

        # Count the order before sending it: if the request fails partway, it still uses up a slot.
        self.orders_this_run += 1
        client_order_id = str(uuid.uuid4())
        try:
            order = self.client.place_market_order(sized.symbol, sized.side, quantity, client_order_id)
            order = self.client.wait_for_order(order["id"])
        except RobinhoodAPIError as e:
            raise OrderRejected(f"Robinhood rejected the order: {e}") from e
        except Exception as e:
            log.error("Order %s state unknown after error: %s", client_order_id, e)
            raise OrderRejected(f"order may or may not have been placed (client_order_id {client_order_id}); "
                                f"do not retry this run. Error: {e}") from e

        state = order.get("state", "unknown")
        filled_qty = float(order.get("filled_asset_quantity") or 0)
        avg_price = float(order.get("average_price") or 0) or sized.price
        fill = Fill(
            timestamp=now_iso(), symbol=sized.symbol, side=sized.side,
            quantity=filled_qty if filled_qty else float(quantity), price=avg_price,
            notional=round((filled_qty or float(quantity)) * avg_price, 2), fee=0.0, reason=reason,
            order_id=str(order.get("id", "")), status=state,
        )
        self.portfolio.fills.append(fill)
        try:
            self.sync()
        except Exception as e:  # the order already went through; don't lose that over a refresh failure
            log.error("Could not refresh account after order %s: %s", fill.order_id, e)
        if state in ("canceled", "failed"):
            raise OrderRejected(f"Robinhood order {fill.order_id} ended as {state}")
        return fill
