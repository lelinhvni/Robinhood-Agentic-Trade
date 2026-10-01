import base64
import json
from unittest.mock import MagicMock

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat

from agentic_trade.market_data import SimulatedMarketData
from agentic_trade.robinhood import RobinhoodCryptoClient, round_to_increment


def make_client(responses):
    key = Ed25519PrivateKey.generate()
    seed = key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    session = MagicMock()
    session.request.side_effect = [MagicMock(status_code=200, json=MagicMock(return_value=r)) for r in responses]
    return RobinhoodCryptoClient("api-key", base64.b64encode(seed).decode(), session=session), key, session


def verify(key, call):
    method, url = call.args
    headers, body = call.kwargs["headers"], call.kwargs["data"] or ""
    path = url.removeprefix(RobinhoodCryptoClient.BASE_URL)
    message = f"api-key{headers['x-timestamp']}{path}{method}{body}".encode()
    key.public_key().verify(base64.b64decode(headers["x-signature"]), message)  # raises if invalid
    return path, body


def test_simulated_is_deterministic_with_seed():
    a = SimulatedMarketData(seed=1).quotes(["BTC-USD"])
    b = SimulatedMarketData(seed=1).quotes(["BTC-USD"])
    assert a == b
    assert a["BTC-USD"].bid < a["BTC-USD"].ask


def test_quotes_are_signed_and_parsed():
    client, key, session = make_client([{"results": [
        {"symbol": "BTC-USD", "price": "100", "bid_inclusive_of_sell_spread": "99.5",
         "ask_inclusive_of_buy_spread": "100.5"},
    ]}])
    quotes = client.quotes(["BTC-USD"])
    assert quotes["BTC-USD"].bid == 99.5 and quotes["BTC-USD"].ask == 100.5
    path, _ = verify(key, session.request.call_args)
    assert path == "/api/v1/crypto/marketdata/best_bid_ask/?symbol=BTC-USD"


def test_order_body_is_signed():
    client, key, session = make_client([{"id": "o1", "state": "open"}])
    client.place_market_order("BTC-USD", "buy", "0.0001", client_order_id="cid")
    path, body = verify(key, session.request.call_args)
    assert path == "/api/v1/crypto/trading/orders/"
    assert json.loads(body) == {"client_order_id": "cid", "side": "buy", "symbol": "BTC-USD", "type": "market",
                                "market_order_config": {"asset_quantity": "0.0001"}}


def test_holdings_follow_pagination():
    client, _, _ = make_client([
        {"results": [{"asset_code": "BTC", "quantity_available_for_trading": "0.5"}],
         "next": RobinhoodCryptoClient.BASE_URL + "/api/v1/crypto/trading/holdings/?cursor=2"},
        {"results": [{"asset_code": "ETH", "quantity_available_for_trading": "2"},
                     {"asset_code": "SOL", "quantity_available_for_trading": "0"}], "next": None},
    ])
    assert client.holdings() == {"BTC-USD": 0.5, "ETH-USD": 2.0}


def test_round_to_increment():
    assert round_to_increment(0.123456789, "0.00001") == "0.12345"
    assert round_to_increment(12.9, "1") == "12"
    assert round_to_increment(0.000004, "0.00001") == "0"
