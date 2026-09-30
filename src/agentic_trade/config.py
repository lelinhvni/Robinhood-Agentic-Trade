"""Runtime settings for the trading agent."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class RiskLimits:
    """Hard limits enforced on every order, regardless of what the model asks for."""

    max_order_notional: float = 1_000.0
    """Largest single order, in USD."""

    max_position_pct: float = 0.25
    """Largest share of total equity any one symbol may occupy after a buy."""

    min_cash_reserve_pct: float = 0.10
    """Share of total equity that must stay in cash after a buy."""

    max_orders_per_run: int = 3
    """Orders the agent may place in a single decision cycle."""


@dataclass(frozen=True)
class Settings:
    symbols: tuple[str, ...] = ("BTC-USD", "ETH-USD", "SOL-USD")
    starting_cash: float = 10_000.0
    fee_rate: float = 0.0
    """Extra fee as a fraction of notional. Robinhood prices its spread into bid/ask, so 0 by default."""

    state_path: Path = Path("state/portfolio.json")
    model: str = "claude-opus-5-5"
    effort: str = "high"
    max_agent_turns: int = 12
    risk: RiskLimits = field(default_factory=RiskLimits)

    @classmethod
    def from_env(cls) -> "Settings":
        symbols = os.environ.get("AGENTIC_TRADE_SYMBOLS")
        state = os.environ.get("AGENTIC_TRADE_STATE")
        kwargs: dict = {}
        if symbols:
            kwargs["symbols"] = tuple(s.strip().upper() for s in symbols.split(",") if s.strip())
        if state:
            kwargs["state_path"] = Path(state)
        return cls(**kwargs)
