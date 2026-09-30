"""Command line entry point: `agentic-trade run|status|reset`."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time

import anthropic

from . import market_data
from .agent import TradingAgent
from .broker import PaperBroker, Portfolio
from .config import Settings


def _history_path(settings: Settings):
    return settings.state_path.with_name("price_history.json")


def cmd_run(args, settings: Settings) -> int:
    feed = market_data.from_env(dict(os.environ), seed=args.seed)
    print(f"Market data: {type(feed).__name__}", file=sys.stderr)
    client = anthropic.Anthropic()

    iteration = 0
    while True:
        iteration += 1
        portfolio = Portfolio.load(settings.state_path, settings.starting_cash)
        history = market_data.PriceHistory(_history_path(settings))
        broker = PaperBroker(portfolio, settings.risk, settings.symbols, settings.fee_rate)
        quotes = feed.quotes(list(settings.symbols))

        result = TradingAgent(client, settings, broker, history).run(quotes)
        portfolio.save(settings.state_path)
        history.save()

        snap = portfolio.snapshot(quotes)
        print(f"\n=== Run {iteration} ({len(result.fills)} fills, stop: {result.stop_reason}) ===")
        print(result.summary)
        print(f"Equity ${snap['equity']:,.2f} | cash ${snap['cash']:,.2f}")

        if args.iterations and iteration >= args.iterations:
            return 0
        time.sleep(args.interval)


def cmd_status(args, settings: Settings) -> int:
    portfolio = Portfolio.load(settings.state_path, settings.starting_cash)
    feed = market_data.from_env(dict(os.environ), seed=args.seed)
    quotes = feed.quotes(list(settings.symbols))
    print(json.dumps(portfolio.snapshot(quotes), indent=2))
    return 0


def cmd_reset(args, settings: Settings) -> int:
    for path in (settings.state_path, _history_path(settings)):
        path.unlink(missing_ok=True)
    print(f"Reset paper portfolio to ${settings.starting_cash:,.2f} cash.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agentic-trade", description="Claude paper-trading agent for crypto.")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--seed", type=int, default=None, help="Seed for simulated prices.")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="Run the agent.")
    run.add_argument("--iterations", type=int, default=1, help="Number of runs; 0 loops forever.")
    run.add_argument("--interval", type=float, default=300, help="Seconds between runs.")
    run.set_defaults(func=cmd_run)
    sub.add_parser("status", help="Show the paper portfolio.").set_defaults(func=cmd_status)
    sub.add_parser("reset", help="Delete saved portfolio and price history.").set_defaults(func=cmd_reset)

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(message)s")
    return args.func(args, Settings.from_env())


if __name__ == "__main__":
    sys.exit(main())
