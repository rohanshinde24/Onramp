from enum import StrEnum


class EscalationReason(StrEnum):
    """Why a case left the normal path. A closed set, so escalations can be counted."""

    OPERATOR_JUDGMENT = "operator_judgment"
    CONFLICTING_EVIDENCE = "conflicting_evidence"
    IDENTITY_UNCERTAIN = "identity_uncertain"
    AMOUNT_OVER_LIMIT = "amount_over_limit"
    EVIDENCE_TIMEOUT = "evidence_timeout"
    INVESTIGATION_FAILED = "investigation_failed"
    INVALID_PROPOSAL = "invalid_proposal"
    POSTCONDITION_FAILED = "postcondition_failed"
