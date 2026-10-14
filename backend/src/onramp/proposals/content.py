"""Proposal request shape, action parameter schemas and the content hash."""

import hashlib
import json
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from onramp.cases.resolutions import ActionType, ResolutionCode

# Evidence references name a marketplace record or a policy passage: "<kind>:<id>".
EVIDENCE_KINDS = frozenset(
    {
        "worker",
        "verification",
        "document",
        "task",
        "assignment",
        "submission",
        "quality_review",
        "payment",
        "payment_event",
        "policy_passage",
    }
)
_REF = re.compile(r"^([a-z_]+):([A-Za-z0-9_.-]{1,64})$")
# Reason codes are upper snake case, such as SETTLEMENT_FAILED.
REASON_CODE_PATTERN = r"^[A-Z][A-Z0-9_]{0,63}$"


class PayoutAdjustmentParams(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    amount_cents: int = Field(gt=0)
    currency: str = Field(pattern=r"^[A-Z]{3}$")


class NoParams(BaseModel):
    """Actions whose target comes entirely from the case record."""

    model_config = ConfigDict(extra="forbid", frozen=True)


ACTION_PARAMS: dict[ActionType, type[BaseModel]] = {
    ActionType.PAYOUT_ADJUSTMENT: PayoutAdjustmentParams,
    ActionType.ACTIVATE_WORKER: NoParams,
    ActionType.OVERTURN_QUALITY_REVIEW: NoParams,
}


class ProposalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resolution_code: ResolutionCode
    action_type: ActionType | None = None
    action_params: dict[str, Any] = Field(default_factory=dict)
    reason_codes: list[str] = Field(default_factory=list, max_length=10)
    evidence_refs: list[str] = Field(min_length=1, max_length=50)
    rationale: str = Field(min_length=1, max_length=5000)

    @field_validator("evidence_refs")
    @classmethod
    def _valid_refs(cls, refs: list[str]) -> list[str]:
        for ref in refs:
            match = _REF.match(ref)
            if match is None or match.group(1) not in EVIDENCE_KINDS:
                raise ValueError(f"malformed evidence reference: {ref!r}")
        return refs

    @field_validator("reason_codes")
    @classmethod
    def _valid_reason_codes(cls, codes: list[str]) -> list[str]:
        for code in codes:
            if not re.fullmatch(REASON_CODE_PATTERN, code):
                raise ValueError(f"malformed reason code: {code!r}")
        return codes


def content_hash(
    *,
    case_id: str,
    resolution_code: str,
    action_type: str | None,
    action_params: dict[str, Any],
    evidence_refs: list[str],
) -> str:
    canonical = json.dumps(
        {
            "case_id": case_id,
            "resolution_code": resolution_code,
            "action_type": action_type,
            "action_params": action_params,
            "evidence_refs": sorted(evidence_refs),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()
