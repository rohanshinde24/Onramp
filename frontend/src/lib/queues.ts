import type { CaseState } from "./types";

// The inbox splits work by what it needs from a person (PRODUCT_SPEC section 5): decisions,
// approvals and escalations are separate queues from cases the system is still working.
export const QUEUES = {
  decide: { label: "Needs decision", states: ["proposed"] },
  approve: { label: "Awaiting approval", states: ["awaiting_approval"] },
  escalated: { label: "Escalated", states: ["escalated"] },
  working: {
    label: "In progress",
    states: [
      "created",
      "initial_checks",
      "investigating",
      "waiting_for_evidence",
      "waiting_for_settlement",
      "executing",
      "verifying",
    ],
  },
  closed: { label: "Closed", states: ["resolved", "closed_no_action"] },
} as const satisfies Record<string, { label: string; states: readonly CaseState[] }>;

export type QueueId = keyof typeof QUEUES;

export function queueOf(raw: string | string[] | undefined): QueueId {
  const value = Array.isArray(raw) ? raw[0] : raw;
  // hasOwn, not `in`: "constructor" and friends are not queues.
  return value && Object.hasOwn(QUEUES, value) ? (value as QueueId) : "decide";
}
