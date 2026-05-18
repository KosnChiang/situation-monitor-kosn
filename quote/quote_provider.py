"""Read-only quote provider abstraction.

This module is the *data side* of the trading pipeline. It is forbidden,
by design and by tests, from importing anything that can place an order:

  * no ``executor.*``, no ``risk.*``, no ``strategy.*``
  * no broker SDK (``shioaji`` / ``ib_insync`` / ``ibapi`` /
    ``MetaTrader5`` / ``ccxt`` / ``binance`` / ``alpaca`` / ``oandapyV20``)
  * no TradingView session / cookie / auth-token reads
  * no MockExecutor calls

A QuoteProvider only knows how to ``fetch(symbol) -> Quote``. The
quote feed CLI (``tools/quote_feed.py``) is the only thing that
should instantiate a provider; the order-execution side reads
``logs/quotes.jsonl`` and never touches a provider object. Process
boundary keeps the two halves isolated.

Defense in depth: ``QuoteProvider.__init__`` refuses to construct if
``LIVE_TRADING`` is anything other than ``"false"``. The quote layer
itself cannot execute anything, but the construction-time check
makes it loud and explicit if the mock envelope is ever flipped in
the wider session.
"""
from __future__ import annotations

import abc
import os
import random
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import ClassVar


class QuoteFeedForbidden(RuntimeError):
    """Raised when configuration tries to enable live trading inside the quote layer."""


@dataclass
class Quote:
    """A single read-only quote snapshot.

    ``last`` is the closest the layer gets to "the price"; consumers
    that need a midpoint should call :pyattr:`mid`. Fields are deliberately
    primitive so the JSONL on-disk contract stays stable across providers.
    """

    symbol: str
    bid: float
    ask: float
    last: float
    ts: float           # unix epoch seconds
    timestamp: str      # ISO 8601 UTC
    source: str         # provider.name -- "mock", "tradingview-ws", etc.

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0

    def to_dict(self) -> dict:
        d = asdict(self)
        d["mid"] = self.mid
        return d


class QuoteProvider(abc.ABC):
    """Abstract read-only quote source. Subclasses implement ``fetch``."""

    name: ClassVar[str] = "abstract"

    def __init__(self) -> None:
        live = (os.getenv("LIVE_TRADING", "false") or "").strip().lower()
        if live != "false":
            raise QuoteFeedForbidden(
                f"QuoteProvider refuses to start: LIVE_TRADING={live!r} (must be 'false'). "
                "Even read-only quote feeds are gated by the mock-mode envelope so a "
                "live-mode session cannot bring a data pipeline up while execution is locked."
            )

    @abc.abstractmethod
    def fetch(self, symbol: str) -> Quote:
        """Return the latest quote for ``symbol``. Must not place orders."""

    def close(self) -> None:
        """Release any provider resources. Default is a no-op."""


class MockQuoteProvider(QuoteProvider):
    """Deterministic mock provider: seeded random walk around ``base_price``.

    Intended for unit tests and for exercising the file-based contract
    end-to-end without touching the network. Never makes a network
    call, never reads a credential.
    """

    name: ClassVar[str] = "mock"

    def __init__(
        self,
        *,
        base_price: float = 2400.0,
        spread: float = 0.5,
        volatility: float = 0.2,
        seed: int = 0,
    ) -> None:
        super().__init__()
        self._rng = random.Random(seed)
        self._spread = float(spread)
        self._volatility = float(volatility)
        self._mid = float(base_price)

    def fetch(self, symbol: str) -> Quote:
        self._mid += self._rng.gauss(0.0, self._volatility)
        bid = self._mid - self._spread / 2.0
        ask = self._mid + self._spread / 2.0
        last = self._mid
        now = time.time()
        iso = datetime.now(timezone.utc).isoformat()
        return Quote(
            symbol=symbol,
            bid=bid,
            ask=ask,
            last=last,
            ts=now,
            timestamp=iso,
            source=self.name,
        )


PROVIDERS: dict[str, type[QuoteProvider]] = {
    "mock": MockQuoteProvider,
}
