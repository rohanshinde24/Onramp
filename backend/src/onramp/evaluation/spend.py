"""What an evaluation run spends on a model provider, and the cap that stops it.

Every model call's usage is priced as it returns. Before each call the meter checks the run's
total against its cap; once the cap is reached, the next call raises instead of being made, and
the run stops. A run can therefore overshoot its cap by at most the cost of one call.

Prices are the provider's list prices per million tokens. A model with no price here cannot be
metered, so a live run with it is refused rather than run blind.
"""

import threading
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from onramp.agent.model import ChatModel, Message, ModelResponse, ToolDefinition, Usage

MILLION = Decimal(1_000_000)
# A five-minute cache write costs 1.25x input; batch processing halves every price.
CACHE_WRITE_MULTIPLIER = Decimal("1.25")
BATCH_DISCOUNT = Decimal("0.5")


@dataclass(frozen=True)
class Price:
    """US dollars per million tokens."""

    input: Decimal
    output: Decimal
    cache_read: Decimal


# Anthropic list prices as published on 2026-10-06. Haiku 5.5's cache-read price is not in that
# table, so it is charged at the full input price: the meter overestimates rather than under.
PRICES: dict[str, Price] = {
    "claude-opus-5-5": Price(Decimal("4.00"), Decimal("20.00"), Decimal("0.20")),
    "claude-sonnet-5-5": Price(Decimal("2.00"), Decimal("10.00"), Decimal("0.20")),
    "claude-haiku-5-5": Price(Decimal("0.10"), Decimal("0.50"), Decimal("0.10")),
}


class UnpricedModelError(ValueError):
    pass


class SpendCapReachedError(RuntimeError):
    """The run has spent its cap. No further model call is made."""


class SpendCapRequiredError(ValueError):
    """A model that costs money was configured without a spending cap."""


def price_for(model_name: str) -> Price | None:
    """The price of a model by its name; ``None`` for a scripted model, which costs nothing."""
    if model_name.startswith("scripted:"):
        return None
    model_id = model_name.split("@", 1)[0]
    try:
        return PRICES[model_id]
    except KeyError:
        raise UnpricedModelError(model_name) from None


def cost(usage: Usage, price: Price | None, *, batch: bool = False) -> Decimal:
    if price is None:
        return Decimal(0)
    uncached = usage.input_tokens - usage.cache_read_tokens - usage.cache_write_tokens
    dollars = (
        uncached * price.input
        + usage.cache_write_tokens * price.input * CACHE_WRITE_MULTIPLIER
        + usage.cache_read_tokens * price.cache_read
        + usage.output_tokens * price.output
    ) / MILLION
    return dollars * BATCH_DISCOUNT if batch else dollars


@dataclass
class SpendMeter:
    """One evaluation run's spend against its cap."""

    # None only for runs whose models are all scripted; a priced model refuses to run without one.
    cap_usd: Decimal | None = None
    spent_usd: Decimal = Decimal(0)
    calls: int = 0
    # Batched investigations charge the meter from several threads.
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def check(self) -> None:
        with self._lock:
            if self.cap_usd is not None and self.spent_usd >= self.cap_usd:
                raise SpendCapReachedError(f"spent ${self.spent_usd:.4f} of a ${self.cap_usd} cap")

    def add(self, dollars: Decimal) -> None:
        with self._lock:
            self.spent_usd += dollars
            self.calls += 1


@dataclass
class MeteredModel:
    """A model whose every call is priced and charged to a meter, per scenario."""

    inner: ChatModel
    meter: SpendMeter
    batch: bool = False
    # What this scenario's answers cost, and the part of that replayed from recordings for free.
    spent_usd: Decimal = Decimal(0)
    replayed_usd: Decimal = Decimal(0)
    usages: list[Usage] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._price = price_for(self.inner.name)
        if self._price is not None and self.meter.cap_usd is None:
            raise SpendCapRequiredError(f"{self.inner.name} costs money; set a spending cap")

    @property
    def name(self) -> str:
        return self.inner.name

    def respond(
        self, system: str, transcript: Sequence[Message], tools: Sequence[ToolDefinition]
    ) -> ModelResponse:
        self.meter.check()
        response = self.inner.respond(system, transcript, tools)
        # A recording model knows what each answer cost when it was made, including any batch
        # discount; a replayed answer is reported at that cost but charges nothing now.
        recorded = getattr(self.inner, "turn_costs", None)
        dollars = recorded[-1] if recorded else cost(response.usage, self._price, batch=self.batch)
        self.spent_usd += dollars
        self.usages.append(response.usage)
        if getattr(self.inner, "last_replayed", False):
            self.replayed_usd += dollars
        else:
            self.meter.add(dollars)
        return response
