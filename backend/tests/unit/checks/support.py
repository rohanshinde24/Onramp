from datetime import UTC, datetime, timedelta
from pathlib import Path

from onramp.checks.policy_context import PolicyContext, from_document
from onramp.policy.corpus import parse

CORPUS = Path(__file__).resolve().parents[4] / "policies"
T0 = datetime(2026, 8, 20, 9, tzinfo=UTC)


def policy(policy_id: str, version: int) -> PolicyContext:
    return from_document(parse(CORPUS / policy_id / f"v{version}.md"))


def days(n: float) -> timedelta:
    return timedelta(days=n)
