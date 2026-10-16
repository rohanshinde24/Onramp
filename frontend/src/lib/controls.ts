import type { Approval, Case, Me, Proposal } from "./types";

// Which controls the console offers. This is presentation only: the API checks permission,
// tenant, state, version, self-approval and content hash on every request, and a control
// shown here by mistake fails there.

export interface Controls {
  runChecks: boolean;
  decide: Proposal | null;
  approve: { proposal: Proposal; overLimit: boolean } | null;
  approveBlocked: string | null;
  propose: boolean;
  escalate: boolean;
  close: boolean;
}

const TERMINAL = new Set(["resolved", "closed_no_action"]);

export function controlsFor(
  kase: Case,
  me: Me,
  proposals: Proposal[],
  approvals: Approval[],
): Controls {
  const can = (permission: string) => me.permissions.includes(permission);
  const pending = proposals.find((p) => p.status === "pending") ?? null;
  const decided = new Set(approvals.map((a) => a.proposal_id));
  const awaiting =
    kase.state === "awaiting_approval"
      ? ([...proposals]
          .reverse()
          .find((p) => p.status === "accepted" && p.action_type && !decided.has(p.id)) ?? null)
      : null;

  let approve: Controls["approve"] = null;
  let approveBlocked: string | null = null;
  if (awaiting) {
    const needed = awaiting.risk_class === "high" ? "approve.high_risk" : "approve.medium_risk";
    if (!can(needed)) {
      approveBlocked =
        awaiting.risk_class === "high"
          ? "A lead approves money movement."
          : "Your role cannot approve this action.";
    } else if (awaiting.proposer_type === "user" && awaiting.proposer_id === me.user_id) {
      approveBlocked = "You proposed this action, so another eligible person must approve it.";
    } else {
      const amount = awaiting.action_params["amount_cents"];
      approve = {
        proposal: awaiting,
        overLimit: typeof amount === "number" && amount > me.payout_approval_limit_cents,
      };
    }
  }

  return {
    runChecks:
      (kase.state === "created" || kase.state === "initial_checks") && can("case.investigate"),
    decide: kase.state === "proposed" && can("proposal.decide") ? pending : null,
    approve,
    approveBlocked,
    propose:
      can("action.propose") &&
      (kase.state === "investigating" ||
        (kase.state === "escalated" && can("case.work_escalated"))),
    escalate: !TERMINAL.has(kase.state) && kase.state !== "escalated" && can("case.escalate"),
    close: kase.state === "escalated" && can("case.work_escalated"),
  };
}
