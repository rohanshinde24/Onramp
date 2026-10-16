// Operator-facing wording for the API's error codes. Anything unknown shows the code itself.
const MESSAGES: Record<string, string> = {
  stale_case: "The case changed since you opened it. Reload to see its current state.",
  illegal_transition: "That step is not allowed from the case's current state.",
  proposal_not_pending: "That proposal has already been decided.",
  not_awaiting_approval: "The case is no longer waiting for this approval.",
  already_decided: "Someone has already decided this approval.",
  content_mismatch:
    "The proposal changed after you loaded it. Reload and review it again before deciding.",
  self_approval: "You proposed this action, so another eligible person must approve it.",
  forbidden: "Your role does not allow this.",
  invalid_proposal: "The proposal was refused.",
  checks_not_applicable: "Checks cannot run in the case's current state.",
  no_applicable_policy: "No policy version was in force at the incident time.",
  evidence_unavailable: "A marketplace record this case depends on is missing.",
  // FastAPI's own request validation, before any of Onramp's rules ran.
  http_422: "The API refused the request as malformed.",
};

export function describeError(code: string, detail: string | null): string {
  const message = Object.hasOwn(MESSAGES, code) ? MESSAGES[code] : code.replace(/_/g, " ");
  return detail && code !== "stale_case" ? `${message} (${detail})` : message;
}
