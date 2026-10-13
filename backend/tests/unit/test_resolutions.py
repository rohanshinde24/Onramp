import pytest

from onramp.cases.models import CaseType
from onramp.cases.resolutions import (
    ACTION_RISK,
    RESOLUTIONS,
    ActionType,
    InvalidResolutionError,
    ResolutionCode,
    RiskClass,
    required_action,
)


def test_every_resolution_belongs_to_exactly_one_case_type() -> None:
    owners = [code for table in RESOLUTIONS.values() for code in table]
    assert sorted(owners) == sorted(ResolutionCode)


def test_every_action_has_a_risk_class_and_is_reachable() -> None:
    assert set(ACTION_RISK) == set(ActionType)
    reachable = {a for table in RESOLUTIONS.values() for a in table.values() if a}
    assert reachable == set(ActionType)


def test_money_movement_is_high_risk() -> None:
    assert ACTION_RISK[ActionType.PAYOUT_ADJUSTMENT] is RiskClass.HIGH


def test_missing_payout_requires_adjustment() -> None:
    action = required_action(CaseType.PAYOUT, ResolutionCode.MISSING_PAYOUT)
    assert action is ActionType.PAYOUT_ADJUSTMENT


def test_paid_in_full_forbids_an_action() -> None:
    assert required_action(CaseType.PAYOUT, ResolutionCode.PAID_IN_FULL) is None


@pytest.mark.parametrize(
    ("case_type", "code"),
    [
        (CaseType.PAYOUT, ResolutionCode.REVIEW_OVERTURNED),
        (CaseType.ONBOARDING, ResolutionCode.MISSING_PAYOUT),
        (CaseType.QUALITY_REVIEW, ResolutionCode.REQUIREMENTS_MET),
    ],
)
def test_resolution_from_another_case_type_is_rejected(
    case_type: CaseType, code: ResolutionCode
) -> None:
    with pytest.raises(InvalidResolutionError):
        required_action(case_type, code)
