"""What an investigation's tools can read, captured so it can run without a database.

Batched investigations run concurrently, but each scenario's world lives alone in the
evaluation databases. So before batching, every case is driven to the point where it needs the
investigator and everything its tools could return is captured: each marketplace read the tools
make (by calling every tool once, and every payment's history), the policy passages in force,
and the keyword ranking over them. The snapshot's toolbox then returns exactly what the
database-backed toolbox would, so answers recorded against it replay through the real runner.

A read the snapshot never captured raises rather than guessing, and that investigation is made
live instead when the run replays.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from onramp.agent.tools import CaseScope, CaseToolbox, NoArgs
from onramp.evaluation.scripted import SnapshotPolicyRetriever
from onramp.marketplace.reader import MarketplaceReader
from onramp.policy.search import RetrievedPassage

Call = tuple[str, tuple[Any, ...]]


class SnapshotMissError(LookupError):
    """The tools asked for a read that was not captured."""


class _CapturingReader:
    def __init__(self, inner: MarketplaceReader) -> None:
        self._inner = inner
        self.calls: dict[Call, Any] = {}

    def __getattr__(self, name: str) -> Callable[..., Any]:
        method = getattr(self._inner, name)

        def capture(*args: Any) -> Any:
            if (name, args) not in self.calls:
                self.calls[(name, args)] = method(*args)
            return self.calls[(name, args)]

        return capture


class _SnapshotReader:
    def __init__(self, calls: dict[Call, Any]) -> None:
        self._calls = calls

    def __getattr__(self, name: str) -> Callable[..., Any]:
        def serve(*args: Any) -> Any:
            try:
                return self._calls[(name, args)]
            except KeyError:
                raise SnapshotMissError(f"{name}{args}") from None

        return serve


@dataclass(frozen=True)
class InvestigationSnapshot:
    scenario_id: str
    scope: CaseScope
    reads: dict[Call, Any]
    passages: tuple[RetrievedPassage, ...]

    def toolbox(self) -> CaseToolbox:
        by_id = {p.passage_id: p for p in self.passages}
        return CaseToolbox(
            self.scope,
            _SnapshotReader(self.reads),
            SnapshotPolicyRetriever(self.passages),
            by_id.get,
        )


def take_snapshot(
    scenario_id: str,
    scope: CaseScope,
    reader: MarketplaceReader,
    passages: list[RetrievedPassage],
) -> InvestigationSnapshot:
    capturing = _CapturingReader(reader)
    toolbox = CaseToolbox(
        scope,
        capturing,
        SnapshotPolicyRetriever(passages),
        {p.passage_id: p for p in passages}.get,
    )
    for spec in toolbox.specs:
        if spec.args is NoArgs:
            toolbox.call(spec.name, {})
    if "get_payments" in {spec.name for spec in toolbox.specs}:
        for payment in toolbox.call("get_payments", {}).content.get("payments", []):
            toolbox.call("get_payment_events", {"payment_id": payment["ref"].split(":", 1)[1]})
    return InvestigationSnapshot(scenario_id, scope, dict(capturing.calls), tuple(passages))
