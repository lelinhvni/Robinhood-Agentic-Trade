import base64
from unittest.mock import MagicMock

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat

from agentic_trade.market_data import RobinhoodCryptoMarketData, SimulatedMarketData


def test_simulated_is_deterministic_with_seed():
    a = SimulatedMarketData(seed=1).quotes(["BTC-USD"])
    b = SimulatedMarketData(seed=1).quotes(["BTC-USD"])
    assert a == b
    assert a["BTC-USD"].bid < a["BTC-USD"].ask


def test_robinhood_signs_requests_and_parses_quotes():
    key = Ed25519PrivateKey.generate()
    seed = key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    session = MagicMock()
    session.get.return_value.json.return_value = {"results": [
        {"symbol": "BTC-USD", "price": "100", "bid_inclusive_of_sell_spread": "99.5",
         "ask_inclusive_of_buy_spread": "100.5"},
    ]}
    client = RobinhoodCryptoMarketData("api-key", base64.b64encode(seed).decode(), session=session)

    quotes = client.quotes(["BTC-USD"])
    assert quotes["BTC-USD"].bid == 99.5 and quotes["BTC-USD"].ask == 100.5

    url = session.get.call_args.args[0]
    headers = session.get.call_args.kwargs["headers"]
    path = url.removeprefix(RobinhoodCryptoMarketData.BASE_URL)
    assert path == "/api/v1/crypto/marketdata/best_bid_ask/?symbol=BTC-USD"
    message = f"api-key{headers['x-timestamp']}{path}GET".encode()
    key.public_key().verify(base64.b64decode(headers["x-signature"]), message)  # raises if invalid
