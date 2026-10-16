import type { CaseType } from "./types";

// Mirrors onramp.cases.resolutions so the manual proposal form offers only resolutions that
// apply to the case. The API validates the proposal against its own catalogue regardless.
export const RESOLUTIONS: Record<CaseType, Record<string, string | null>> = {
  payout: {
    MISSING_PAYOUT: "PAYOUT_ADJUSTMENT",
    UNDERPAID: "PAYOUT_ADJUSTMENT",
    PAID_IN_FULL: null,
    NOT_ELIGIBLE: null,
  },
  onboarding: {
    REQUIREMENTS_MET: "ACTIVATE_WORKER",
    REQUIREMENT_FAILED: null,
    ALREADY_ACTIVE: null,
  },
  quality_review: {
    REVIEW_UPHELD: null,
    REVIEW_OVERTURNED: "OVERTURN_QUALITY_REVIEW",
    OUTSIDE_DISPUTE_WINDOW: null,
  },
};

// The escalation reasons a person chooses; the rest are set by the system on failures.
export const OPERATOR_ESCALATIONS = [
  "operator_judgment",
  "policy_referral",
  "conflicting_evidence",
  "identity_uncertain",
] as const;
