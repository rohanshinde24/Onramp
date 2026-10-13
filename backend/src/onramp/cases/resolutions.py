"""Closed catalogue of resolutions, actions and risk (PRODUCT_SPEC sections 3, 6, 7).

A resolution ends a case. Each one either requires a specific action or forbids any action.
Interim outcomes that make a case wait (settlement pending, missing requirement) are not
resolutions; they come from checks or investigation and move the case into a waiting state.
"""

from enum import StrEnum

from onramp.cases.models import CaseType


class ResolutionCode(StrEnum):
    MISSING_PAYOUT = "MISSING_PAYOUT"
    UNDERPAID = "UNDERPAID"
    PAID_IN_FULL = "PAID_IN_FULL"
    NOT_ELIGIBLE = "NOT_ELIGIBLE"
    REQUIREMENTS_MET = "REQUIREMENTS_MET"
    REQUIREMENT_FAILED = "REQUIREMENT_FAILED"
    ALREADY_ACTIVE = "ALREADY_ACTIVE"
    REVIEW_UPHELD = "REVIEW_UPHELD"
    REVIEW_OVERTURNED = "REVIEW_OVERTURNED"
    OUTSIDE_DISPUTE_WINDOW = "OUTSIDE_DISPUTE_WINDOW"


class ActionType(StrEnum):
    PAYOUT_ADJUSTMENT = "PAYOUT_ADJUSTMENT"
    ACTIVATE_WORKER = "ACTIVATE_WORKER"
    OVERTURN_QUALITY_REVIEW = "OVERTURN_QUALITY_REVIEW"


class RiskClass(StrEnum):
    MEDIUM = "medium"
    HIGH = "high"


R, A = ResolutionCode, ActionType

# Resolution -> the action it requires, or None if it must not carry an action.
RESOLUTIONS: dict[CaseType, dict[ResolutionCode, ActionType | None]] = {
    CaseType.PAYOUT: {
        R.MISSING_PAYOUT: A.PAYOUT_ADJUSTMENT,
        R.UNDERPAID: A.PAYOUT_ADJUSTMENT,
        R.PAID_IN_FULL: None,
        R.NOT_ELIGIBLE: None,
    },
    CaseType.ONBOARDING: {
        R.REQUIREMENTS_MET: A.ACTIVATE_WORKER,
        R.REQUIREMENT_FAILED: None,
        R.ALREADY_ACTIVE: None,
    },
    CaseType.QUALITY_REVIEW: {
        R.REVIEW_UPHELD: None,
        R.REVIEW_OVERTURNED: A.OVERTURN_QUALITY_REVIEW,
        R.OUTSIDE_DISPUTE_WINDOW: None,
    },
}

# Every action changes marketplace state and needs a human approver of this class.
ACTION_RISK: dict[ActionType, RiskClass] = {
    A.PAYOUT_ADJUSTMENT: RiskClass.HIGH,
    A.ACTIVATE_WORKER: RiskClass.MEDIUM,
    A.OVERTURN_QUALITY_REVIEW: RiskClass.MEDIUM,
}


class InvalidResolutionError(ValueError):
    pass


def required_action(case_type: CaseType, resolution: ResolutionCode) -> ActionType | None:
    """The action a resolution requires. Raises if the resolution does not apply."""
    allowed = RESOLUTIONS[case_type]
    if resolution not in allowed:
        raise InvalidResolutionError(f"{resolution.value} is not a {case_type.value} resolution")
    return allowed[resolution]
