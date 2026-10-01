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


def test_keygen_writes_private_key_and_prints_matching_public_key(tmp_path, capsys):
    import base64
    import os

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    out = tmp_path / "rh.env"
    assert main(["keygen", "--out", str(out)]) == 0
    assert oct(os.stat(out).st_mode & 0o777) == "0o600"

    private_b64 = out.read_text().strip().split("=", 1)[1]
    printed = capsys.readouterr().out
    public_b64 = printed.split("\n\n")[1].strip()
    key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(private_b64))
    assert base64.b64encode(key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode() == public_b64
    assert private_b64 not in printed

    with pytest.raises(SystemExit, match="already exists"):
        main(["keygen", "--out", str(out)])


def test_approver_declines_without_terminal(monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    order = SizedOrder("BTC-USD", "buy", 0.01, 100.0, 0.0)
    assert _approver(auto_approve=False)(order, "r") is False
    assert _approver(auto_approve=True)(order, "r") is True
