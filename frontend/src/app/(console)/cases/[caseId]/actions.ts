"use server";

import { refresh } from "next/cache";

import type { ActionState } from "@/components/action-form";
import { post } from "@/lib/api";
import { RESOLUTIONS } from "@/lib/catalogue";
import { describeError } from "@/lib/errors";
import type { CaseType } from "@/lib/types";

// Every action re-reads the session cookie inside post() and the API decides whether it is
// allowed. Arguments bound from the page are identifiers, not authority.

async function send(path: string, body: unknown): Promise<ActionState> {
  const result = await post(path, body);
  if (!result.ok) return { error: describeError(result.error.code, result.error.detail) };
  refresh();
  return { error: null };
}

const text = (form: FormData, name: string) => String(form.get(name) ?? "").trim();
const enc = encodeURIComponent;

export async function runChecks(caseId: string, version: number): Promise<ActionState> {
  return send(`/cases/${enc(caseId)}/checks`, { expected_version: version });
}

export async function acceptProposal(
  proposalId: string,
  _: ActionState,
  form: FormData,
): Promise<ActionState> {
  return send(`/proposals/${enc(proposalId)}/accept`, { note: text(form, "note") });
}

export async function rejectProposal(
  proposalId: string,
  _: ActionState,
  form: FormData,
): Promise<ActionState> {
  const note = text(form, "note");
  if (!note) return { error: "Say why the proposal is wrong; the investigation sees it." };
  return send(`/proposals/${enc(proposalId)}/reject`, { note });
}

export async function decideApproval(
  proposalId: string,
  contentHash: string,
  _: ActionState,
  form: FormData,
): Promise<ActionState> {
  const decision = text(form, "decision");
  if (decision !== "approved" && decision !== "rejected") return { error: "Choose a decision." };
  const note = text(form, "note");
  if (decision === "rejected" && !note) return { error: "Say why the action is rejected." };
  // The hash of the proposal as rendered for the approver: if the proposal changed since, the
  // API refuses with content_mismatch instead of approving something nobody saw.
  return send(`/proposals/${enc(proposalId)}/approval`, {
    decision,
    content_hash: contentHash,
    note,
  });
}

export async function escalate(
  caseId: string,
  version: number,
  _: ActionState,
  form: FormData,
): Promise<ActionState> {
  return send(`/cases/${enc(caseId)}/escalate`, {
    reason: text(form, "reason"),
    expected_version: version,
    note: text(form, "note"),
  });
}

export async function closeCase(
  caseId: string,
  version: number,
  _: ActionState,
  form: FormData,
): Promise<ActionState> {
  const note = text(form, "note");
  if (!note) return { error: "Say why the case closes without action." };
  return send(`/cases/${enc(caseId)}/close`, { expected_version: version, note });
}

export async function proposeManually(
  caseId: string,
  version: number,
  caseType: CaseType,
  _: ActionState,
  form: FormData,
): Promise<ActionState> {
  const resolution = text(form, "resolution");
  if (!Object.hasOwn(RESOLUTIONS[caseType], resolution)) return { error: "Choose a resolution." };
  const actionType = RESOLUTIONS[caseType][resolution];
  let actionParams: Record<string, unknown> = {};
  if (actionType === "PAYOUT_ADJUSTMENT") {
    const amount = Number(text(form, "amount"));
    if (!Number.isFinite(amount) || amount <= 0) return { error: "Enter the amount owed." };
    // Dollars in the form, integer cents on the wire. The gateway recomputes the amount owed
    // and refuses a proposal that differs.
    actionParams = { amount_cents: Math.round(amount * 100), currency: text(form, "currency") };
  }
  const evidence = form.getAll("evidence").map(String);
  if (evidence.length === 0) return { error: "Cite at least one record or passage." };
  const reasonCodes = text(form, "reason_codes")
    .split(/[\s,]+/)
    .filter(Boolean)
    .map((c) => c.toUpperCase());
  return send(`/cases/${enc(caseId)}/proposals`, {
    expected_version: version,
    proposal: {
      resolution_code: resolution,
      action_type: actionType,
      action_params: actionParams,
      reason_codes: reasonCodes,
      evidence_refs: evidence,
      rationale: text(form, "rationale"),
    },
  });
}
