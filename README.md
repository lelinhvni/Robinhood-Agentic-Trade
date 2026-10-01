# Robinhood Agentic Trade

A Claude-driven crypto trading agent. Each run, Claude reviews the portfolio and recent prices, then decides to buy, sell or hold.

- **Paper mode (the default)** trades a simulated portfolio against live Robinhood quotes or simulated prices. No real orders are sent.
- **Live mode (`--live`)** places real market orders on your Robinhood crypto account through the official [Crypto Trading API](https://docs.robinhood.com/crypto/trading/). Every order still has to pass the same risk limits, and by default you approve each one at the terminal.

## How it works

```
quotes (Robinhood Crypto API or simulated)
        │
        ▼
TradingAgent ── Claude (tool use) ──► get_portfolio / get_quotes / get_price_history / place_order
                                                                            │
                                         ┌──────────────────────────────────┴───────────┐
                                         ▼                                              ▼
                          PaperBroker: risk checks → simulated fill    LiveBroker: risk checks → your approval
                                                                        → Robinhood market order → re-sync account
```

- **`agent.py`** runs the Claude tool-use loop (`claude-opus-5-5`, adaptive thinking, strict tool schemas, server-side refusal fallback). Quotes are fixed for the length of a run.
- **`broker.py`** holds the shared risk checks (`check_order`) and the paper broker. Every order, paper or live, is checked against:
  - allowed symbols only
  - maximum order size
  - maximum share of equity per symbol
  - minimum cash reserve
  - maximum orders per run
  - no shorting

  When an order breaks a limit, Claude gets back a tool error that explains why.
- **`live_broker.py`** places real orders. Before sending, it rounds the quantity to Robinhood's increment and checks Robinhood's minimum and maximum order sizes. Then it asks for approval, waits for the fill, and re-reads cash and holdings from Robinhood.
- **`robinhood.py`** is the signed (Ed25519) API client: account, holdings, trading pairs, quotes and orders.
- **`market_data.py`** has the price simulator and the per-run price history.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env   # then fill in, and export the variables
```

| Variable | Purpose |
|---|---|
| `ANTHROPIC_API_KEY` | Claude API access. Not needed if you've already run `ant auth login`. |
| `ROBINHOOD_API_KEY`, `ROBINHOOD_PRIVATE_KEY_BASE64` | Robinhood Crypto API credentials, created at robinhood.com under crypto API settings. Optional in paper mode, where they only supply live quotes. Required for `--live`. |
| `ROBINHOOD_EXPECTED_ACCOUNT` | Live mode refuses to trade if the API key belongs to a different crypto account. Recommended. |
| `AGENTIC_TRADE_LIVE_MAX_ORDER_USD` | Per-order cap in live mode. Default `5`. |
| `AGENTIC_TRADE_SYMBOLS` | Comma-separated symbols. Default: `BTC-USD,ETH-USD,SOL-USD`. |
| `AGENTIC_TRADE_STATE` | Paper portfolio path. Default: `state/portfolio.json`. Live fills are logged next to it in `live_portfolio.json`. |

Starting cash, risk limits, model and effort live in `src/agentic_trade/config.py`.

## Usage

```bash
# Paper
agentic-trade -v run                                # one decision cycle
agentic-trade -v run --iterations 0 --interval 900  # loop every 15 minutes
agentic-trade status
agentic-trade reset

# Live: real money
agentic-trade status --live                         # account, buying power, holdings
agentic-trade -v run --live                         # prompts y/N before each order
agentic-trade -v run --live --yes                   # places orders without asking. Use with care.
```

**Before you go live:**
- Run `status --live` first. It confirms the API key works and shows which account it trades.
- The order and holdings formats in `robinhood.py` were written from Robinhood's docs and tested only against fakes. Your first live order should be a few dollars.
- In live mode without `--yes`, an order is declined if there's no terminal to ask for approval. Scheduled or unattended runs therefore trade only with `--yes`.
- Each run makes several Claude API calls, and every call is billed.

## Tests

```bash
pytest
```

The tests replace Claude and Robinhood with fakes, so they need no API key or network and place no orders.
