import pytest

from agentic_trade.broker import OrderRejected, PaperBroker, Portfolio
from agentic_trade.config import RiskLimits
from agentic_trade.market_data import Quote

QUOTES = {"BTC-USD": Quote("BTC-USD", bid=99.0, ask=101.0), "ETH-USD": Quote("ETH-USD", bid=9.9, ask=10.1)}
SYMBOLS = ("BTC-USD", "ETH-USD")


def make_broker(cash=10_000.0, **risk):
    return PaperBroker(Portfolio(cash=cash), RiskLimits(**risk), SYMBOLS)


def test_buy_fills_at_ask_and_sell_at_bid():
    b = make_broker()
    fill = b.place_market_order("btc-usd", "buy", 1000, QUOTES)
    assert fill.price == 101.0
    assert b.portfolio.cash == pytest.approx(9_000)
    qty = b.portfolio.holdings["BTC-USD"]
    assert qty == pytest.approx(1000 / 101)

    b.place_market_order("BTC-USD", "sell", qty * 99, QUOTES)
    assert "BTC-USD" not in b.portfolio.holdings
    assert b.portfolio.cash == pytest.approx(9_000 + 1000 / 101 * 99)


@pytest.mark.parametrize("kwargs, match", [
    (dict(symbol="DOGE-USD", side="buy", notional_usd=10), "allowed symbol"),
    (dict(symbol="BTC-USD", side="hold", notional_usd=10), "side"),
    (dict(symbol="BTC-USD", side="buy", notional_usd=-5), "positive"),
    (dict(symbol="BTC-USD", side="buy", notional_usd=5_000), "max order size"),
    (dict(symbol="BTC-USD", side="sell", notional_usd=10), "no shorting"),
])
def test_rejections(kwargs, match):
    with pytest.raises(OrderRejected, match=match):
        make_broker(max_order_notional=1_000).place_market_order(quotes=QUOTES, **kwargs)


def test_position_cap():
    b = make_broker(cash=1_000, max_order_notional=1_000, max_position_pct=0.25, min_cash_reserve_pct=0)
    b.place_market_order("BTC-USD", "buy", 200, QUOTES)
    with pytest.raises(OrderRejected, match="cap"):
        b.place_market_order("BTC-USD", "buy", 100, QUOTES)


def test_cash_reserve():
    b = make_broker(cash=1_000, max_order_notional=1_000, max_position_pct=1.0, min_cash_reserve_pct=0.5)
    with pytest.raises(OrderRejected, match="cash reserve"):
        b.place_market_order("BTC-USD", "buy", 600, QUOTES)


def test_orders_per_run_limit():
    b = make_broker(max_orders_per_run=1)
    b.place_market_order("ETH-USD", "buy", 100, QUOTES)
    with pytest.raises(OrderRejected, match="order limit"):
        b.place_market_order("ETH-USD", "buy", 100, QUOTES)


def test_portfolio_roundtrip(tmp_path):
    b = make_broker()
    b.place_market_order("ETH-USD", "buy", 500, QUOTES, reason="test")
    path = tmp_path / "p.json"
    b.portfolio.save(path)
    loaded = Portfolio.load(path, starting_cash=1)
    assert loaded.cash == b.portfolio.cash
    assert loaded.holdings == b.portfolio.holdings
    assert loaded.fills[0].reason == "test"
    assert Portfolio.load(tmp_path / "missing.json", 123).cash == 123
