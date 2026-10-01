"""Command line entry point: `agentic-trade run|status|reset`."""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import os
import sys
import time

import anthropic

from . import market_data
from .agent import TradingAgent
from .broker import PaperBroker, Portfolio, SizedOrder
from .config import Settings
from .live_broker import LiveBroker, mask
from .robinhood import RobinhoodAPIError, RobinhoodCryptoClient


def _history_path(state_path):
    return state_path.with_name(state_path.stem + "_price_history.json")


def _robinhood_client() -> RobinhoodCryptoClient:
    key, secret = os.environ.get("ROBINHOOD_API_KEY"), os.environ.get("ROBINHOOD_PRIVATE_KEY_BASE64")
    if not (key and secret):
        sys.exit("Live mode needs ROBINHOOD_API_KEY and ROBINHOOD_PRIVATE_KEY_BASE64.")
    return RobinhoodCryptoClient(key, secret)


def _approver(auto_approve: bool):
    def approve(order: SizedOrder, reason: str) -> bool:
        summary = (f"LIVE ORDER: {order.side.upper()} {order.quantity} {order.symbol} "
                   f"(~${order.quantity * order.price:,.2f} at {order.price:,.2f})\n  reason: {reason}")
        print(summary, file=sys.stderr)
        if auto_approve:
            print("  auto-approved", file=sys.stderr)
            return True
        if not sys.stdin.isatty():
            print("  declined: --confirm needs a terminal to ask for approval", file=sys.stderr)
            return False
        return input("  Place this order? [y/N] ").strip().lower() in ("y", "yes")
    return approve


def _live_broker(settings: Settings, auto_approve: bool) -> LiveBroker:
    client = _robinhood_client()
    risk = dataclasses.replace(settings.risk, max_order_notional=min(settings.risk.max_order_notional,
                                                                      settings.live_max_order_notional))
    portfolio = Portfolio.load(settings.live_state_path, starting_cash=0.0)
    broker = LiveBroker(client, portfolio, risk, settings.symbols, _approver(auto_approve),
                        settings.expected_account)
    broker.sync()
    return broker


def cmd_run(args, settings: Settings) -> int:
    client = anthropic.Anthropic()

    if args.live:
        auto_approve = settings.live_auto_approve and not args.confirm
        broker = _live_broker(settings, auto_approve)
        feed = broker.client
        state_path = settings.live_state_path
        print(f"LIVE TRADING on Robinhood crypto account {mask(broker.account['account_number'])} | "
              f"buying power ${broker.portfolio.cash:,.2f} | max ${broker.risk.max_order_notional:,.2f}/order | "
              f"{'auto-approve' if auto_approve else 'manual approval'}", file=sys.stderr)
    else:
        feed = market_data.from_env(dict(os.environ), seed=args.seed)
        state_path = settings.state_path
        print(f"Paper trading | market data: {type(feed).__name__}", file=sys.stderr)

    iteration = 0
    while True:
        iteration += 1
        history = market_data.PriceHistory(_history_path(state_path))
        if args.live:
            if iteration > 1:
                broker.sync()
        else:
            portfolio = Portfolio.load(state_path, settings.starting_cash)
            broker = PaperBroker(portfolio, settings.risk, settings.symbols, settings.fee_rate)
        quotes = feed.quotes(list(settings.symbols))

        result = TradingAgent(client, settings, broker, history, live=args.live).run(quotes)
        broker.portfolio.save(state_path)
        history.save()

        snap = broker.portfolio.snapshot(quotes)
        print(f"\n=== Run {iteration} ({len(result.fills)} fills, stop: {result.stop_reason}) ===")
        print(result.summary)
        print(f"Equity ${snap['equity']:,.2f} | cash ${snap['cash']:,.2f}")

        if args.iterations and iteration >= args.iterations:
            return 0
        time.sleep(args.interval)


def cmd_status(args, settings: Settings) -> int:
    if args.live:
        broker = _live_broker(settings, auto_approve=False)
        quotes = broker.client.quotes(list(settings.symbols))
        snap = broker.portfolio.snapshot(quotes)
        snap["account"] = mask(broker.account["account_number"])
    else:
        portfolio = Portfolio.load(settings.state_path, settings.starting_cash)
        feed = market_data.from_env(dict(os.environ), seed=args.seed)
        snap = portfolio.snapshot(feed.quotes(list(settings.symbols)))
    print(json.dumps(snap, indent=2))
    return 0


def cmd_keygen(args, settings: Settings) -> int:
    """Create an Ed25519 key pair for the Robinhood Crypto Trading API."""
    import base64
    from pathlib import Path

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat

    out = Path(args.out).expanduser()
    if out.exists() and not args.force:
        sys.exit(f"{out} already exists. Use --force to overwrite it (the old key stops working on Robinhood).")

    key = Ed25519PrivateKey.generate()
    private_b64 = base64.b64encode(key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())).decode()
    public_b64 = base64.b64encode(key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()

    out.parent.mkdir(parents=True, exist_ok=True)
    out.touch(mode=0o600, exist_ok=True)
    out.chmod(0o600)
    out.write_text(f"ROBINHOOD_PRIVATE_KEY_BASE64={private_b64}\n")

    print("Public key (paste this into Robinhood when creating the API key):\n")
    print(f"  {public_b64}\n")
    print(f"Private key saved to {out} (readable only by you). Never share it or commit it.")
    print(f"Load it with:  set -a; source {out}; set +a")
    return 0


def cmd_reset(args, settings: Settings) -> int:
    for path in (settings.state_path, _history_path(settings.state_path)):
        path.unlink(missing_ok=True)
    print(f"Reset paper portfolio to ${settings.starting_cash:,.2f} cash. Live trade logs are untouched.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agentic-trade", description="Claude trading agent for crypto.")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--seed", type=int, default=None, help="Seed for simulated prices.")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="Run the agent.")
    run.add_argument("--iterations", type=int, default=1, help="Number of runs; 0 loops forever.")
    run.add_argument("--interval", type=float, default=300, help="Seconds between runs.")
    run.add_argument("--live", action="store_true", help="Trade real money on Robinhood (default: paper).")
    run.add_argument("--confirm", action="store_true", help="With --live, ask y/N before each order.")
    run.set_defaults(func=cmd_run)
    status = sub.add_parser("status", help="Show the portfolio.")
    status.add_argument("--live", action="store_true", help="Show the live Robinhood account.")
    status.set_defaults(func=cmd_status)
    sub.add_parser("reset", help="Delete the saved paper portfolio and price history.").set_defaults(func=cmd_reset)
    keygen = sub.add_parser("keygen", help="Create a key pair for the Robinhood Crypto Trading API.")
    keygen.add_argument("--out", default="~/.config/agentic-trade/robinhood.env",
                        help="Where to save the private key (default: %(default)s).")
    keygen.add_argument("--force", action="store_true", help="Overwrite an existing key file.")
    keygen.set_defaults(func=cmd_keygen)

    args = parser.parse_args(argv)
    if getattr(args, "confirm", False) and not args.live:
        parser.error("--confirm only applies with --live")
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(message)s")
    try:
        return args.func(args, Settings.from_env())
    except (RuntimeError, RobinhoodAPIError) as e:
        sys.exit(f"Error: {e}")


if __name__ == "__main__":
    sys.exit(main())
