import { describe, expect, it } from "vitest";

import { controlsFor } from "./controls";
import type { Approval, Case, Me, Proposal } from "./types";

const OPERATOR = [
  "case.read",
  "case.create",
  "case.investigate",
  "case.escalate",
  "proposal.decide",
  "action.propose",
  "approve.medium_risk",
  "audit.read",
];
const LEAD = [...OPERATOR, "approve.high_risk", "case.work_escalated"];

function me(role: Me["role"], permissions: string[], userId = `acme_${role}`): Me {
  return {
    user_id: userId,
    tenant_id: "acme",
    role,
    permissions,
    display_name: userId,
    tenant_name: "Acme",
    payout_approval_limit_cents: 50_000,
  };
}

const operator = me("operator", OPERATOR);
const lead = me("lead", LEAD);
const admin = me("admin", ["case.read", "users.manage", "audit.read"]);

function kase(state: Case["state"]): Case {
  return {
    id: "case_1",
    case_type: "payout",
    state,
    version: 4,
    subject: "submission:s_1",
    worker_id: "w_1",
    incident_at: "2026-08-20T09:00:00Z",
    incident_basis: "submission_time",
    reported_description: "",
    assignee_id: null,
    resolution_code: null,
    escalation_reason: null,
    created_at: "2026-08-20T09:00:00Z",
    updated_at: "2026-08-20T09:00:00Z",
  };
}

function proposal(over: Partial<Proposal> = {}): Proposal {
  return {
    id: "prop_1",
    case_id: "case_1",
    proposer_type: "agent",
    proposer_id: null,
    resolution_code: "MISSING_PAYOUT",
    action_type: "PAYOUT_ADJUSTMENT",
    action_params: { amount_cents: 8400, currency: "USD" },
    risk_class: "high",
    reason_codes: [],
    evidence_refs: ["submission:s_1"],
    rationale: "settlement failed",
    content_hash: "a".repeat(64),
    status: "accepted",
    created_at: "2026-08-20T10:00:00Z",
    decided_at: null,
    decided_by_id: null,
    decision_note: null,
    ...over,
  };
}

describe("controlsFor", () => {
  it("offers a pending proposal for decision only while the case is proposed", () => {
    const pending = proposal({ status: "pending" });
    expect(controlsFor(kase("proposed"), operator, [pending], []).decide).toBe(pending);
    expect(controlsFor(kase("investigating"), operator, [pending], []).decide).toBeNull();
    expect(controlsFor(kase("proposed"), admin, [pending], []).decide).toBeNull();
  });

  it("lets a lead approve money movement but not an operator", () => {
    const p = proposal();
    const byLead = controlsFor(kase("awaiting_approval"), lead, [p], []);
    const byOperator = controlsFor(kase("awaiting_approval"), operator, [p], []);

    expect(byLead.approve?.proposal).toBe(p);
    expect(byOperator.approve).toBeNull();
    expect(byOperator.approveBlocked).toMatch(/lead/);
  });

  it("lets an operator approve a medium-risk action", () => {
    const p = proposal({ action_type: "ACTIVATE_WORKER", risk_class: "medium", action_params: {} });
    expect(controlsFor(kase("awaiting_approval"), operator, [p], []).approve?.proposal).toBe(p);
  });

  it("never offers approval to the person who proposed the action", () => {
    const p = proposal({ proposer_type: "user", proposer_id: "acme_lead" });
    const controls = controlsFor(kase("awaiting_approval"), lead, [p], []);

    expect(controls.approve).toBeNull();
    expect(controls.approveBlocked).toMatch(/another eligible person/);
  });

  it("does not offer an approval that was already decided", () => {
    const p = proposal();
    const decided: Approval = {
      id: "appr_1",
      proposal_id: p.id,
      approver_id: "acme_lead",
      decision: "approved",
      risk_class: "high",
      content_hash: p.content_hash,
      note: "",
      decided_at: "2026-08-20T11:00:00Z",
    };
    expect(controlsFor(kase("awaiting_approval"), lead, [p], [decided]).approve).toBeNull();
  });

  it("warns when an adjustment is above the tenant's approval limit", () => {
    const p = proposal({ action_params: { amount_cents: 60_000, currency: "USD" } });
    expect(controlsFor(kase("awaiting_approval"), lead, [p], []).approve?.overLimit).toBe(true);
  });

  it("keeps escalated cases with leads", () => {
    expect(controlsFor(kase("escalated"), operator, [], []).propose).toBe(false);
    expect(controlsFor(kase("escalated"), operator, [], []).close).toBe(false);
    expect(controlsFor(kase("escalated"), lead, [], []).propose).toBe(true);
    expect(controlsFor(kase("escalated"), lead, [], []).close).toBe(true);
  });

  it("offers nothing on a closed case", () => {
    const c = controlsFor(kase("resolved"), lead, [], []);
    expect([c.runChecks, c.decide, c.approve, c.propose, c.escalate, c.close]).toEqual([
      false,
      null,
      null,
      false,
      false,
      false,
    ]);
  });

  it("offers checks on a new case to people who investigate", () => {
    expect(controlsFor(kase("created"), operator, [], []).runChecks).toBe(true);
    expect(controlsFor(kase("created"), admin, [], []).runChecks).toBe(false);
  });
});
