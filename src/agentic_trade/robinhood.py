"""Signed client for Robinhood's official Crypto Trading API.

Docs: https://docs.robinhood.com/crypto/trading/

Every request is signed with the account's Ed25519 private key over
`api_key + timestamp + path + method + body`.
"""

from __future__ import annotations

import base64
import json
import time
import uuid
from decimal import ROUND_DOWN, Decimal
from urllib.parse import urlencode

import requests
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .market_data import Quote


class RobinhoodAPIError(Exception):
    pass


class RobinhoodCryptoClient:
    BASE_URL = "https://trading.robinhood.com"

    def __init__(self, api_key: str, private_key_base64: str, session: requests.Session | None = None):
        seed = base64.b64decode(private_key_base64)
        # Robinhood's key generator prints the 32-byte seed; accept a 64-byte seed||public key too.
        self._key = Ed25519PrivateKey.from_private_bytes(seed[:32])
        self._api_key = api_key
        self._session = session or requests.Session()

    # -- transport --------------------------------------------------------------------------------

    def _headers(self, method: str, path: str, body: str = "") -> dict[str, str]:
        timestamp = str(int(time.time()))
        message = f"{self._api_key}{timestamp}{path}{method}{body}"
        signature = base64.b64encode(self._key.sign(message.encode())).decode()
        return {
            "x-api-key": self._api_key,
            "x-signature": signature,
            "x-timestamp": timestamp,
            "Content-Type": "application/json; charset=utf-8",
        }

    def _request(self, method: str, path: str, payload: dict | None = None) -> dict:
        body = json.dumps(payload) if payload is not None else ""
        resp = self._session.request(method, self.BASE_URL + path, headers=self._headers(method, path, body),
                                     data=body or None, timeout=10)
        if resp.status_code >= 400:
            raise RobinhoodAPIError(f"{method} {path} -> HTTP {resp.status_code}: {resp.text[:500]}")
        return resp.json()

    def _get_all(self, path: str) -> list[dict]:
        """Follow `next` links on paginated list endpoints."""
        results: list[dict] = []
        while path:
            data = self._request("GET", path)
            results.extend(data.get("results", []))
            nxt = data.get("next")
            path = nxt.removeprefix(self.BASE_URL) if nxt else ""
        return results

    # -- market data ------------------------------------------------------------------------------

    def quotes(self, symbols: list[str]) -> dict[str, Quote]:
        query = urlencode([("symbol", s) for s in symbols])
        data = self._request("GET", f"/api/v1/crypto/marketdata/best_bid_ask/?{query}")
        out: dict[str, Quote] = {}
        for row in data.get("results", []):
            out[row["symbol"]] = Quote(
                symbol=row["symbol"],
                bid=float(row["bid_inclusive_of_sell_spread"]),
                ask=float(row["ask_inclusive_of_buy_spread"]),
            )
        missing = set(symbols) - out.keys()
        if missing:
            raise LookupError(f"No quote returned for: {', '.join(sorted(missing))}")
        return out

    # -- trading ----------------------------------------------------------------------------------

    def account(self) -> dict:
        """{account_number, status, buying_power, buying_power_currency}"""
        return self._request("GET", "/api/v1/crypto/trading/accounts/")

    def holdings(self) -> dict[str, float]:
        """Sellable quantity per pair symbol, e.g. {"BTC-USD": 0.0012}."""
        rows = self._get_all("/api/v1/crypto/trading/holdings/")
        return {f"{r['asset_code']}-USD": float(r["quantity_available_for_trading"]) for r in rows
                if float(r.get("quantity_available_for_trading") or 0) > 0}

    def trading_pair(self, symbol: str) -> dict:
        """{symbol, asset_increment, min_order_size, max_order_size, status, ...}"""
        rows = self._get_all(f"/api/v1/crypto/trading/trading_pairs/?{urlencode({'symbol': symbol})}")
        if not rows:
            raise LookupError(f"Unknown trading pair {symbol}")
        return rows[0]

    def place_market_order(self, symbol: str, side: str, asset_quantity: str,
                           client_order_id: str | None = None) -> dict:
        payload = {
            "client_order_id": client_order_id or str(uuid.uuid4()),
            "side": side,
            "symbol": symbol,
            "type": "market",
            "market_order_config": {"asset_quantity": asset_quantity},
        }
        return self._request("POST", "/api/v1/crypto/trading/orders/", payload)

    def get_order(self, order_id: str) -> dict:
        return self._request("GET", f"/api/v1/crypto/trading/orders/{order_id}/")

    def wait_for_order(self, order_id: str, timeout: float = 15.0, poll: float = 1.0) -> dict:
        """Poll until the order leaves the open/pending states or the timeout passes."""
        deadline = time.monotonic() + timeout
        order = self.get_order(order_id)
        while order.get("state") in ("open", "pending", "partially_filled") and time.monotonic() < deadline:
            time.sleep(poll)
            order = self.get_order(order_id)
        return order


def round_to_increment(quantity: float, increment: str) -> str:
    """Round a quantity down to the pair's asset increment, as a plain decimal string."""
    step = Decimal(increment)
    rounded = (Decimal(str(quantity)) / step).to_integral_value(rounding=ROUND_DOWN) * step
    return format(rounded.normalize(), "f")
