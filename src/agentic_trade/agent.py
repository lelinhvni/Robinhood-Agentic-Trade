"""The Claude decision loop.

Each run: fetch one set of quotes, give Claude tools to inspect the paper
portfolio and price history and to place orders, and loop until it finishes.
Every order goes through `PaperBroker`, which enforces the risk limits; a
rejected order comes back to Claude as a tool error it can react to.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

import anthropic

from .broker import Fill, OrderRejected, PaperBroker
from .config import Settings
from .market_data import PriceHistory, Quote

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You manage a paper-trading crypto portfolio. No real money is at stake, but trade as if it were: \
the goal is steady risk-adjusted growth, not activity.

Each run you get fresh quotes. Use the tools to review the portfolio and recent price history, \
then decide whether to buy, sell, or hold. Holding is often the right call; only trade when you \
can state a concrete reason. Orders are market orders sized in USD notional: buys fill at the ask, \
sells at the bid, so every round trip pays the spread.

The broker enforces hard risk limits and rejects orders that break them. If an order is rejected, \
read the reason and adjust or move on; don't retry the same order.

When you're done, reply with a short summary: what you did, why, and what you'd watch next run."""

TOOLS: list[dict[str, Any]] = [
    {
        "name": "get_portfolio",
        "description": "Current cash, total equity, open positions valued at the bid, and the 10 most recent fills.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "get_quotes",
        "description": "Current bid, ask, and mid price for every tradable symbol. Prices are fixed for this run.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "get_price_history",
        "description": "Mid prices recorded at the start of previous runs for one symbol, oldest first, "
                       "as [timestamp, price] pairs. Empty on the first run.",
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "Symbol such as BTC-USD."},
                "limit": {"type": "integer", "description": "Maximum points to return (1-200)."},
            },
            "required": ["symbol", "limit"],
            "additionalProperties": False,
        },
    },
    {
        "name": "place_order",
        "description": "Place a paper market order sized in USD. Returns the fill, or an error explaining "
                       "which risk limit rejected it.",
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "Symbol such as BTC-USD."},
                "side": {"type": "string", "enum": ["buy", "sell"]},
                "notional_usd": {"type": "number", "description": "Order size in US dollars."},
                "reason": {"type": "string", "description": "One sentence on why you're placing this order."},
            },
            "required": ["symbol", "side", "notional_usd", "reason"],
            "additionalProperties": False,
        },
    },
]


@dataclass
class RunResult:
    summary: str
    fills: list[Fill] = field(default_factory=list)
    stop_reason: str | None = None
    turns: int = 0


class TradingAgent:
    def __init__(self, client: anthropic.Anthropic, settings: Settings, broker: PaperBroker,
                 history: PriceHistory):
        self.client = client
        self.settings = settings
        self.broker = broker
        self.history = history

    def _run_tool(self, name: str, args: dict, quotes: dict[str, Quote], fills: list[Fill]) -> str:
        if name == "get_portfolio":
            return json.dumps(self.broker.portfolio.snapshot(quotes))
        if name == "get_quotes":
            return json.dumps([q.to_dict() for q in quotes.values()])
        if name == "get_price_history":
            limit = max(1, min(int(args.get("limit", 50)), 200))
            return json.dumps(self.history.get(str(args["symbol"]).upper(), limit))
        if name == "place_order":
            fill = self.broker.place_market_order(
                symbol=str(args["symbol"]), side=str(args["side"]),
                notional_usd=float(args["notional_usd"]), quotes=quotes, reason=str(args.get("reason", "")),
            )
            fills.append(fill)
            log.info("FILL %s %s %.8f @ %.2f ($%.2f) - %s", fill.side, fill.symbol, fill.quantity,
                     fill.price, fill.notional, fill.reason)
            return json.dumps(asdict(fill))
        raise ValueError(f"unknown tool {name!r}")

    def run(self, quotes: dict[str, Quote]) -> RunResult:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.history.record(quotes, now)
        self.broker.orders_this_run = 0

        limits = self.settings.risk
        user_msg = (
            f"New run at {now}. Tradable symbols: {', '.join(self.settings.symbols)}.\n"
            f"Risk limits: max order ${limits.max_order_notional:,.0f}; max {limits.max_position_pct:.0%} "
            f"of equity per symbol; keep at least {limits.min_cash_reserve_pct:.0%} in cash; "
            f"at most {limits.max_orders_per_run} orders this run."
        )
        messages: list[dict[str, Any]] = [{"role": "user", "content": user_msg}]
        fills: list[Fill] = []
        tools = [{**t, "strict": True} for t in TOOLS]

        for turn in range(1, self.settings.max_agent_turns + 1):
            response = self.client.beta.messages.create(
                model=self.settings.model,
                max_tokens=16000,
                system=SYSTEM_PROMPT,
                tools=tools,
                messages=messages,
                thinking={"type": "adaptive"},
                output_config={"effort": self.settings.effort},
                # On a safety decline, the API re-runs the request on a fallback model.
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
            messages.append({"role": "assistant", "content": response.content})
            text = "\n".join(b.text for b in response.content if b.type == "text").strip()

            if response.stop_reason == "refusal":
                category = response.stop_details.category if response.stop_details else None
                return RunResult(f"Model declined the request (category: {category}).", fills, "refusal", turn)
            if response.stop_reason == "pause_turn":
                continue
            if response.stop_reason != "tool_use":
                return RunResult(text, fills, response.stop_reason, turn)

            results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                try:
                    content, is_error = self._run_tool(block.name, block.input, quotes, fills), False
                except OrderRejected as e:
                    log.info("REJECTED %s: %s", block.input, e)
                    content, is_error = f"Order rejected: {e}", True
                except (KeyError, TypeError, ValueError) as e:
                    content, is_error = f"Invalid tool call: {e}", True
                results.append({"type": "tool_result", "tool_use_id": block.id,
                                "content": content, "is_error": is_error})
            messages.append({"role": "user", "content": results})

        return RunResult("Stopped: reached the turn limit for this run.", fills, "turn_limit",
                         self.settings.max_agent_turns)
