from types import SimpleNamespace as NS

from agentic_trade.agent import TradingAgent
from agentic_trade.broker import PaperBroker, Portfolio
from agentic_trade.config import Settings
from agentic_trade.market_data import PriceHistory, Quote

QUOTES = {"BTC-USD": Quote("BTC-USD", bid=99.0, ask=101.0)}


def tool_use(id, name, input):
    return NS(type="tool_use", id=id, name=name, input=input)


def text(t):
    return NS(type="text", text=t)


class FakeClient:
    """Stands in for anthropic.Anthropic, replaying scripted responses."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.beta = NS(messages=NS(create=self._create))

    def _create(self, **kwargs):
        self.calls.append({**kwargs, "messages": list(kwargs["messages"])})
        return self.responses.pop(0)


def make_agent(client, tmp_path):
    settings = Settings(symbols=("BTC-USD",), state_path=tmp_path / "p.json")
    broker = PaperBroker(Portfolio(cash=10_000), settings.risk, settings.symbols)
    return TradingAgent(client, settings, broker, PriceHistory(tmp_path / "h.json")), broker


def test_agent_places_order_and_reports_rejection(tmp_path):
    client = FakeClient([
        NS(stop_reason="tool_use", stop_details=None, content=[
            tool_use("t1", "get_portfolio", {}),
            tool_use("t2", "place_order", {"symbol": "BTC-USD", "side": "buy", "notional_usd": 500, "reason": "dip"}),
            tool_use("t3", "place_order", {"symbol": "BTC-USD", "side": "buy", "notional_usd": 50_000, "reason": "yolo"}),
        ]),
        NS(stop_reason="end_turn", stop_details=None, content=[text("Bought $500 BTC.")]),
    ])
    agent, broker = make_agent(client, tmp_path)
    result = agent.run(QUOTES)

    assert result.summary == "Bought $500 BTC."
    assert result.stop_reason == "end_turn"
    assert len(result.fills) == 1
    assert broker.portfolio.cash == 9_500

    results = client.calls[1]["messages"][-1]["content"]
    assert [r["is_error"] for r in results] == [False, False, True]
    assert "max order size" in results[2]["content"]

    first = client.calls[0]
    assert first["model"] == "claude-opus-5-5"
    assert first["fallbacks"] == "default"
    assert all(t["strict"] for t in first["tools"])
    assert agent.history.get("BTC-USD")[0][1] == 100.0


def test_agent_stops_on_refusal(tmp_path):
    client = FakeClient([NS(stop_reason="refusal", stop_details=NS(category="cyber"), content=[])])
    agent, _ = make_agent(client, tmp_path)
    result = agent.run(QUOTES)
    assert result.stop_reason == "refusal"
    assert "cyber" in result.summary


def test_agent_turn_limit(tmp_path):
    loop = NS(stop_reason="tool_use", stop_details=None, content=[tool_use("t", "get_quotes", {})])
    client = FakeClient([loop] * 20)
    agent, _ = make_agent(client, tmp_path)
    result = agent.run(QUOTES)
    assert result.stop_reason == "turn_limit"
    assert len(client.calls) == agent.settings.max_agent_turns
