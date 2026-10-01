import pytest

from agentic_trade.broker import SizedOrder
from agentic_trade.cli import _approver, main


def test_confirm_requires_live():
    with pytest.raises(SystemExit):
        main(["run", "--confirm"])


def test_live_auto_approve_defaults_on_and_env_can_disable(monkeypatch):
    from agentic_trade.config import Settings
    monkeypatch.delenv("AGENTIC_TRADE_LIVE_AUTO_APPROVE", raising=False)
    assert Settings.from_env().live_auto_approve is True
    monkeypatch.setenv("AGENTIC_TRADE_LIVE_AUTO_APPROVE", "0")
    assert Settings.from_env().live_auto_approve is False


def test_live_without_credentials_exits(monkeypatch):
    monkeypatch.delenv("ROBINHOOD_API_KEY", raising=False)
    monkeypatch.delenv("ROBINHOOD_PRIVATE_KEY_BASE64", raising=False)
    with pytest.raises(SystemExit, match="ROBINHOOD_API_KEY"):
        main(["status", "--live"])


def test_approver_declines_without_terminal(monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    order = SizedOrder("BTC-USD", "buy", 0.01, 100.0, 0.0)
    assert _approver(auto_approve=False)(order, "r") is False
    assert _approver(auto_approve=True)(order, "r") is True
