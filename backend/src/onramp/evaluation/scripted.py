"""Scripted investigators for case evaluation, and the retrieval they run against.

A script is a recorded investigation: the tool calls a model would make, in order, and the
conclusion it would submit. Scripts run the real investigation graph, tools, validation and
everything downstream without a model provider, so evaluation of the application is
deterministic and free. They say nothing about how well any real model investigates.
"""

from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from onramp.agent.model import ModelResponse, ScriptedModel, ToolCall
from onramp.policy.applicability import applicable_versions_for_domain
from onramp.policy.models import PolicyPassage
from onramp.policy.search import RetrievedPassage


class ScriptedToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tool: str = Field(min_length=1)
    args: dict[str, Any] = Field(default_factory=dict)


class ScriptSet(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int = Field(ge=1)
    model: str = Field(min_length=1)
    # Scenario ID -> turns; each turn is the tool calls of one model response.
    investigations: dict[str, tuple[tuple[ScriptedToolCall, ...], ...]]

    @property
    def model_name(self) -> str:
        return f"scripted:{self.model}:v{self.version}"

    def model_for(self, scenario_id: str) -> ScriptedModel:
        """A fresh model for one scenario. A scenario without a script gets a model that fails
        if called, so an unexpected investigation is visible rather than silently scripted."""
        turns = self.investigations.get(scenario_id, ())
        return ScriptedModel(
            [
                ModelResponse(
                    tool_calls=tuple(
                        ToolCall(f"call_{i}_{j}", step.tool, dict(step.args))
                        for j, step in enumerate(turn)
                    )
                )
                for i, turn in enumerate(turns)
            ],
            name=self.model_name,
        )


def load_scripts(path: Path) -> ScriptSet:
    return ScriptSet.model_validate(yaml.safe_load(path.read_text()))


def passages_in_force(session: Session, domain: str, at: datetime) -> list[RetrievedPassage]:
    """Every passage of the policy versions in force for ``domain`` at ``at``."""
    versions = {
        (v.policy_id, v.version) for v in applicable_versions_for_domain(session, domain, at)
    }
    return [
        RetrievedPassage(p.passage_id, p.policy_id, p.version, p.section, p.heading, p.text, 1.0)
        for p in session.scalars(select(PolicyPassage))
        if (p.policy_id, p.version) in versions
    ]


def rank_by_keywords(
    passages: Sequence[RetrievedPassage], query: str, k: int
) -> list[RetrievedPassage]:
    """Deterministic word-overlap ranking, ties broken by passage ID."""
    words = {w.strip(".,?").lower() for w in query.split() if len(w) > 3}
    ranked = sorted(
        passages,
        key=lambda p: (-len(words & set(f"{p.heading} {p.text}".lower().split())), p.passage_id),
    )
    return ranked[:k]


class KeywordPolicyRetriever:
    """Deterministic word-overlap ranking over the passages in force, read from PostgreSQL.

    Scripted runs use it so they need no search cluster. It is not the production retriever.
    """

    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def search(self, domain: str, at: datetime, query: str, k: int = 5) -> list[RetrievedPassage]:
        with self._sessions() as session:
            return rank_by_keywords(passages_in_force(session, domain, at), query, k)


class SnapshotPolicyRetriever:
    """The same ranking over a fixed list of passages, with no database."""

    def __init__(self, passages: Sequence[RetrievedPassage]) -> None:
        self._passages = tuple(passages)

    def search(self, domain: str, at: datetime, query: str, k: int = 5) -> list[RetrievedPassage]:
        return rank_by_keywords(self._passages, query, k)
