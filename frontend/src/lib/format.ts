import type { CaseState, CaseType } from "./types";

export const STATE_LABEL: Record<CaseState, string> = {
  created: "Created",
  initial_checks: "Running checks",
  investigating: "Investigating",
  waiting_for_evidence: "Waiting for evidence",
  waiting_for_settlement: "Waiting for settlement",
  proposed: "Needs decision",
  awaiting_approval: "Awaiting approval",
  executing: "Executing",
  verifying: "Verifying",
  escalated: "Escalated",
  resolved: "Resolved",
  closed_no_action: "Closed, no action",
};

export const TYPE_LABEL: Record<CaseType, string> = {
  payout: "Payout",
  onboarding: "Onboarding",
  quality_review: "Quality review",
};

/** Money is integer cents and an ISO currency everywhere; never a float until display. */
export function money(cents: number, currency: string): string {
  return new Intl.NumberFormat("en-US", { style: "currency", currency }).format(cents / 100);
}

export function dateTime(iso: string): string {
  return new Intl.DateTimeFormat("en-US", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "UTC",
  }).format(new Date(iso)) + " UTC";
}

/** "3d 4h", "5h 12m", "8m": how long a case has been open, for queue triage. */
export function age(fromIso: string, now: Date): string {
  const minutes = Math.max(0, Math.floor((now.getTime() - new Date(fromIso).getTime()) / 60000));
  const days = Math.floor(minutes / 1440);
  const hours = Math.floor((minutes % 1440) / 60);
  if (days > 0) return `${days}d ${hours}h`;
  if (hours > 0) return `${hours}h ${minutes % 60}m`;
  return `${minutes}m`;
}

/** "payout_v12_section_4_3" -> "payout v12 §4.3". */
export function passageLabel(passageId: string): string {
  const match = /^([a-z_]+)_v(\d+)_section_([\d_]+)$/.exec(passageId);
  if (!match) return passageId;
  return `${match[1].replace(/_/g, " ")} v${match[2]} §${match[3].replace(/_/g, ".")}`;
}

// Where the incident time that selects the policy version came from (PRODUCT_SPEC section 4).
const INCIDENT_BASIS: Record<string, string> = {
  "submission.submitted_at": "when the work was submitted",
  "review.reviewed_at": "when the review was made",
  "case.reported_at": "when the worker reported it",
};

export function incidentBasis(basis: string): string {
  return Object.hasOwn(INCIDENT_BASIS, basis) ? INCIDENT_BASIS[basis] : basis;
}

export function humanize(code: string): string {
  const words = code.replace(/[._]/g, " ").toLowerCase().trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

export function percent(value: number | null | undefined, digits = 1): string {
  return value == null ? "n/a" : `${(value * 100).toFixed(digits)}%`;
}
