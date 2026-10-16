// Shapes returned by the Onramp API. The backend's Pydantic models are authoritative.

export type CaseState =
  | "created"
  | "initial_checks"
  | "investigating"
  | "waiting_for_evidence"
  | "waiting_for_settlement"
  | "proposed"
  | "awaiting_approval"
  | "executing"
  | "verifying"
  | "escalated"
  | "resolved"
  | "closed_no_action";

export type CaseType = "payout" | "onboarding" | "quality_review";

export interface Me {
  user_id: string;
  tenant_id: string;
  role: "operator" | "lead" | "admin" | "marketplace_service";
  permissions: string[];
  display_name: string;
  tenant_name: string;
  payout_approval_limit_cents: number;
}

export interface Case {
  id: string;
  case_type: CaseType;
  state: CaseState;
  version: number;
  subject: string;
  worker_id: string;
  incident_at: string;
  incident_basis: string;
  reported_description: string;
  assignee_id: string | null;
  resolution_code: string | null;
  escalation_reason: string | null;
  created_at: string;
  updated_at: string;
}

export interface CasePage {
  cases: Case[];
  next_before: string | null;
}

export interface TimelineEntry {
  event_id: string;
  occurred_at: string;
  actor_type: string;
  actor_id: string | null;
  action: string;
  details: Record<string, unknown>;
}

export interface Proposal {
  id: string;
  case_id: string;
  proposer_type: "user" | "agent" | "system";
  proposer_id: string | null;
  resolution_code: string;
  action_type: string | null;
  action_params: Record<string, unknown>;
  risk_class: "medium" | "high" | null;
  reason_codes: string[];
  evidence_refs: string[];
  rationale: string;
  content_hash: string;
  status: "pending" | "accepted" | "rejected";
  created_at: string;
  decided_at: string | null;
  decided_by_id: string | null;
  decision_note: string | null;
}

export interface Approval {
  id: string;
  proposal_id: string;
  approver_id: string;
  decision: "approved" | "rejected";
  risk_class: string;
  content_hash: string;
  note: string;
  decided_at: string;
}

export interface CheckRun {
  id: string;
  case_id: string;
  case_version: number;
  policy_id: string | null;
  policy_version: number | null;
  outcome: string;
  reason_codes: string[];
  evidence_refs: string[];
  citations: string[];
  findings: string[];
  missing_items: string[];
  wait_until: string | null;
  error: string | null;
  proposal_id: string | null;
  created_at: string;
}

export interface Conclusion {
  decision: "resolve" | "escalate";
  resolution_code?: string | null;
  escalation_reason?: string | null;
  confidence?: number;
  reason_codes?: string[];
  citations?: string[];
  evidence_refs?: string[];
  proposed_action?: {
    action_type: string;
    amount_cents?: number | null;
    currency?: string | null;
  } | null;
  rationale?: string;
}

export interface TrajectoryStep {
  step: number;
  kind: "model" | "tool" | "validation" | string;
  tool?: string;
  tool_calls?: string[];
  arguments?: Record<string, unknown>;
  refs?: string[];
  passages?: string[];
  error?: string | null;
  problems?: string[];
  input_tokens?: number;
  output_tokens?: number;
  ms?: number;
}

export interface InvestigationRun {
  id: string;
  case_id: string;
  case_version: number;
  model_name: string;
  prompt_version: string;
  status: string;
  conclusion: Conclusion | null;
  problems: string[];
  model_calls: number;
  input_tokens: number;
  output_tokens: number;
  elapsed_s: number;
  trajectory: TrajectoryStep[];
  proposal_id: string | null;
  created_at: string;
}

export interface ExecutedAction {
  id: string;
  case_id: string;
  proposal_id: string;
  approval_id: string;
  action_type: string;
  idempotency_key: string;
  request: Record<string, unknown>;
  status: "executing" | "succeeded" | "failed" | "unverified";
  result: Record<string, unknown> | null;
  failure_code: string | null;
  failure_message: string | null;
  created_at: string;
  completed_at: string | null;
}

export interface EvidenceItem {
  ref: string;
  kind: string;
  source: "marketplace";
  record: Record<string, unknown>;
}

export interface Passage {
  passage_id: string;
  policy_id: string;
  version: number;
  section: string;
  heading: string;
  text: string;
}

export interface AppliedPolicy {
  policy_id: string;
  version: number;
  effective_from: string;
  effective_until: string | null;
  parameters: Record<string, unknown>;
}

export interface CasePolicy {
  case_id: string;
  incident_at: string;
  incident_basis: string;
  versions: AppliedPolicy[];
}

export interface ApiErrorBody {
  error: string;
  detail: string | null;
}
