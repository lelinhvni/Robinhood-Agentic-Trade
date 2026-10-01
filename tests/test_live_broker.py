import pytest

from agentic_trade.broker import OrderRejected, Portfolio
from agentic_trade.config import RiskLimits
from agentic_trade.live_broker import LiveBroker
from agentic_trade.market_data import Quote
from agentic_trade.robinhood import RobinhoodAPIError

QUOTES = {"BTC-USD": Quote("BTC-USD", bid=99.0, ask=100.0)}
PAIR = {"symbol": "BTC-USD", "asset_increment": "0.0001", "min_order_size": "0.0001",
        "max_order_size": "100", "status": "tradable"}


class FakeRobinhood:
    def __init__(self, cash=15.0, account_number="111", order_state="filled", fail=None):
        self.cash = cash
        self.account_number = account_number
        self.order_state = order_state
        self.fail = fail
        self.orders = []

    def account(self):
        return {"account_number": self.account_number, "status": "active", "buying_power": str(self.cash)}

    def holdings(self):
        return {"BTC-USD": sum(float(q) for _, _, q in self.orders)} if self.orders else {}

    def trading_pair(self, symbol):
        return PAIR

    def place_market_order(self, symbol, side, asset_quantity, client_order_id=None):
        if self.fail:
            raise self.fail
        self.orders.append((symbol, side, asset_quantity))
        self.cash -= float(asset_quantity) * 100
        return {"id": "ord-1"}

    def wait_for_order(self, order_id):
        qty = self.orders[-1][2]
        return {"id": order_id, "state": self.order_state, "filled_asset_quantity": qty, "average_price": "100"}


def make(fake, approve=lambda o, r: True, expected=None, **risk):
    risk = RiskLimits(**{"max_order_notional": 5, "max_position_pct": 1, "min_cash_reserve_pct": 0, **risk})
    broker = LiveBroker(fake, Portfolio(cash=0), risk, ("BTC-USD",), approve, expected)
    broker.sync()
    return broker


def test_places_rounded_order_and_resyncs():
    fake = FakeRobinhood()
    broker = make(fake)
    fill = broker.place_market_order("BTC-USD", "buy", 4.0, QUOTES, reason="test")
    assert fake.orders == [("BTC-USD", "buy", "0.04")]
    assert fill.order_id == "ord-1" and fill.status == "filled" and fill.price == 100
    assert broker.portfolio.cash == pytest.approx(11.0)
    assert broker.portfolio.holdings == {"BTC-USD": 0.04}
    assert broker.portfolio.fills[-1].reason == "test"


def test_risk_limit_applies_before_anything_is_sent():
    fake = FakeRobinhood()
    with pytest.raises(OrderRejected, match="max order size"):
        make(fake).place_market_order("BTC-USD", "buy", 10.0, QUOTES)
    assert fake.orders == []


def test_declined_by_human_sends_nothing():
    fake = FakeRobinhood()
    with pytest.raises(OrderRejected, match="declined"):
        make(fake, approve=lambda o, r: False).place_market_order("BTC-USD", "buy", 4.0, QUOTES)
    assert fake.orders == []


def test_below_minimum_order_size():
    fake = FakeRobinhood()
    with pytest.raises(OrderRejected, match="minimum"):
        make(fake).place_market_order("BTC-USD", "buy", 0.001, QUOTES)


def test_wrong_account_refuses_to_start():
    with pytest.raises(RuntimeError, match="Refusing to trade"):
        make(FakeRobinhood(account_number="999"), expected="111")


def test_api_error_becomes_rejection():
    fake = FakeRobinhood(fail=RobinhoodAPIError("HTTP 400"))
    broker = make(fake)
    with pytest.raises(OrderRejected, match="Robinhood rejected"):
        broker.place_market_order("BTC-USD", "buy", 4.0, QUOTES)
    assert broker.orders_this_run == 1


def test_canceled_order_is_logged_and_reported():
    broker = make(FakeRobinhood(order_state="canceled"))
    with pytest.raises(OrderRejected, match="canceled"):
        broker.place_market_order("BTC-USD", "buy", 4.0, QUOTES)
    assert broker.portfolio.fills[-1].status == "canceled"
