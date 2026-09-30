# Robinhood Agentic Trade

A Claude-driven **paper-trading** agent for crypto. Each run, Claude reviews a simulated portfolio and recent prices, then decides to buy, sell or hold. Orders fill against live Robinhood quotes (or simulated prices) and never reach a real brokerage account.

> This project never places real orders. Its Robinhood integration only reads market data.

## How it works

```
quotes (Robinhood Crypto API or simulated)
        │
        ▼
TradingAgent ── Claude (tool use) ──► get_portfolio / get_quotes / get_price_history / place_order
        │                                                                   │
        │                                                                   ▼
        │                                            PaperBroker: risk checks → fill or reject
        ▼
state/portfolio.json, state/price_history.json
```

- **`agent.py`** runs the Claude tool-use loop (`claude-opus-5-5`, adaptive thinking, strict tool schemas, server-side refusal fallback). Quotes are fixed for the length of a run, so every fill in a run sees the same prices.
- **`broker.py`** is the paper account. Buys fill at the ask and sells fill at the bid. Every order is checked against hard limits:
  - allowed symbols only
  - maximum order size
  - maximum share of equity per symbol
  - minimum cash reserve
  - maximum orders per run
  - no shorting

  When an order breaks a limit, Claude gets back a tool error that explains why.
- **`market_data.py`** has two price sources:
  - A signed client for Robinhood's official Crypto Trading API. It reads best bid/ask only.
  - A seeded random-walk simulator for offline use.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env   # then fill in, and export the variables
```

| Variable | Purpose |
|---|---|
| `ANTHROPIC_API_KEY` | Claude API access. Not needed if you've already run `ant auth login`. |
| `ROBINHOOD_API_KEY`, `ROBINHOOD_PRIVATE_KEY_BASE64` | Optional. Enables live quotes from the [Robinhood Crypto API](https://docs.robinhood.com/crypto/trading/). Without them, prices are simulated. |
| `AGENTIC_TRADE_SYMBOLS` | Comma-separated symbols. Default: `BTC-USD,ETH-USD,SOL-USD`. |
| `AGENTIC_TRADE_STATE` | Portfolio file path. Default: `state/portfolio.json`. |

Starting cash, risk limits, model and effort live in `src/agentic_trade/config.py`.

## Usage

```bash
agentic-trade -v run                              # one decision cycle
agentic-trade -v run --iterations 0 --interval 900  # loop every 15 minutes
agentic-trade status                              # portfolio snapshot
agentic-trade reset                               # back to starting cash
```

Each run makes several Claude API calls, and every call is billed.

## Tests

```bash
pytest
```

The tests replace Claude with a fake client, so they need no API key or network.
