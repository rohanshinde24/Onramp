import { ActionForm } from "@/components/action-form";
import { Badge, Fields, RefList, Section } from "@/components/ui";
import { OPERATOR_ESCALATIONS, RESOLUTIONS } from "@/lib/catalogue";
import type { Controls } from "@/lib/controls";
import { humanize, money } from "@/lib/format";
import type { Case, EvidenceItem, Me, Proposal } from "@/lib/types";

import {
  acceptProposal,
  closeCase,
  decideApproval,
  escalate,
  proposeManually,
  rejectProposal,
  runChecks,
} from "./actions";

const input = "block w-full rounded border border-border bg-surface p-1.5";

function Note({ required, label }: { required?: boolean; label: string }) {
  return (
    <label className="block">
      <span className="text-muted">{label}</span>
      <textarea name="note" rows={2} required={required} maxLength={2000} className={input} />
    </label>
  );
}

export function ProposalSummary({ proposal, me }: { proposal: Proposal; me: Me }) {
  const amount = proposal.action_params["amount_cents"];
  const currency = String(proposal.action_params["currency"] ?? "USD");
  return (
    <Fields
      rows={[
        ["Resolution", <strong key="r">{proposal.resolution_code}</strong>],
        [
          "Action",
          proposal.action_type ? (
            <span key="a" className="flex items-center gap-2">
              {proposal.action_type}
              {proposal.risk_class && (
                <Badge tone={proposal.risk_class === "high" ? "danger" : "attention"}>
                  {proposal.risk_class} risk
                </Badge>
              )}
            </span>
          ) : (
            "None"
          ),
        ],
        ...(typeof amount === "number"
          ? ([
              [
                "Amount",
                <span key="m">
                  {money(amount, currency)}
                  {amount > me.payout_approval_limit_cents && (
                    <span className="ml-2">
                      <Badge tone="danger">
                        over the {money(me.payout_approval_limit_cents, currency)} limit
                      </Badge>
                    </span>
                  )}
                </span>,
              ],
            ] as [string, React.ReactNode][])
          : []),
        [
          "Proposed by",
          proposal.proposer_type === "user" ? (proposal.proposer_id ?? "user") : proposal.proposer_type,
        ],
        ["Reason codes", proposal.reason_codes.join(", ") || "none"],
        ["Evidence", <RefList key="e" refs={proposal.evidence_refs} />],
        ["Rationale", <span key="ra" className="whitespace-pre-wrap">{proposal.rationale}</span>],
        ["Content hash", <code key="h" className="text-xs">{proposal.content_hash}</code>],
      ]}
    />
  );
}

export function DecisionPanel({
  kase,
  me,
  controls,
  evidence,
  citedPassages,
}: {
  kase: Case;
  me: Me;
  controls: Controls;
  evidence: EvidenceItem[];
  citedPassages: string[];
}) {
  const panels: React.ReactNode[] = [];

  if (controls.runChecks) {
    panels.push(
      <div key="checks" className="space-y-1">
        <p className="text-muted">
          Run the deterministic checks for this case type under the policy in force.
        </p>
        <ActionForm action={runChecks.bind(null, kase.id, kase.version)} submit="Run checks" />
      </div>,
    );
  }

  if (controls.decide) {
    const p = controls.decide;
    panels.push(
      <div key="decide" className="space-y-3">
        <ProposalSummary proposal={p} me={me} />
        <div className="grid gap-4 md:grid-cols-2">
          <ActionForm
            action={acceptProposal.bind(null, p.id)}
            submit={p.action_type ? "Accept and send for approval" : "Accept and resolve"}
          >
            <Note label="Note (optional)" />
          </ActionForm>
          <ActionForm action={rejectProposal.bind(null, p.id)} submit="Reject" tone="secondary">
            <Note required label="Why is it wrong? (required)" />
          </ActionForm>
        </div>
      </div>,
    );
  }

  if (controls.approve) {
    const { proposal: p, overLimit } = controls.approve;
    panels.push(
      <div key="approve" className="space-y-3">
        <ProposalSummary proposal={p} me={me} />
        {overLimit && (
          <p className="text-red-700 dark:text-red-400">
            This amount is above your tenant&apos;s approval limit. Approving escalates the case
            instead of executing it.
          </p>
        )}
        <ActionForm action={decideApproval.bind(null, p.id, p.content_hash)} submit="Record decision">
          <fieldset className="flex gap-4">
            <label>
              <input type="radio" name="decision" value="approved" required /> Approve
            </label>
            <label>
              <input type="radio" name="decision" value="rejected" /> Reject
            </label>
          </fieldset>
          <Note label="Note (required to reject)" />
        </ActionForm>
        <p className="text-xs text-muted">
          Your decision is bound to content hash <code>{p.content_hash.slice(0, 12)}</code>. The
          Action Gateway executes only this exact proposal.
        </p>
      </div>,
    );
  } else if (controls.approveBlocked) {
    panels.push(
      <p key="blocked" className="text-muted">
        Awaiting approval. {controls.approveBlocked}
      </p>,
    );
  }

  if (controls.propose) {
    const resolutions = Object.entries(RESOLUTIONS[kase.case_type]);
    const refs = [...evidence.map((e) => e.ref), ...citedPassages.map((p) => `policy_passage:${p}`)];
    panels.push(
      <details key="propose" className="rounded border border-border p-3">
        <summary className="cursor-pointer font-medium">Propose a resolution manually</summary>
        <div className="mt-3">
          <ActionForm
            action={proposeManually.bind(null, kase.id, kase.version, kase.case_type)}
            submit="Propose"
          >
            <label className="block">
              <span className="text-muted">Resolution</span>
              <select name="resolution" required className={input}>
                {resolutions.map(([code, action]) => (
                  <option key={code} value={code}>
                    {code}
                    {action ? ` (requires ${action})` : ""}
                  </option>
                ))}
              </select>
            </label>
            {kase.case_type === "payout" && (
              <div className="flex gap-2">
                <label className="block flex-1">
                  <span className="text-muted">Amount owed, if adjusting</span>
                  <input name="amount" inputMode="decimal" className={input} />
                </label>
                <label className="block w-24">
                  <span className="text-muted">Currency</span>
                  <input name="currency" defaultValue="USD" pattern="[A-Z]{3}" className={input} />
                </label>
              </div>
            )}
            <fieldset>
              <legend className="text-muted">Evidence and policy cited</legend>
              <div className="mt-1 grid gap-0.5 md:grid-cols-2">
                {refs.map((ref) => (
                  <label key={ref} className="mono text-xs">
                    <input type="checkbox" name="evidence" value={ref} /> {ref}
                  </label>
                ))}
              </div>
            </fieldset>
            <label className="block">
              <span className="text-muted">Reason codes (optional)</span>
              <input name="reason_codes" placeholder="UPPER_SNAKE_CASE, comma separated" className={input} />
            </label>
            <label className="block">
              <span className="text-muted">Rationale</span>
              <textarea name="rationale" rows={3} required maxLength={5000} className={input} />
            </label>
          </ActionForm>
        </div>
      </details>,
    );
  }

  if (controls.close) {
    panels.push(
      <details key="close" className="rounded border border-border p-3">
        <summary className="cursor-pointer font-medium">Close without action</summary>
        <div className="mt-3">
          <ActionForm action={closeCase.bind(null, kase.id, kase.version)} submit="Close case" tone="secondary">
            <Note required label="Why (duplicate, withdrawn, ...)" />
          </ActionForm>
        </div>
      </details>,
    );
  }

  if (controls.escalate) {
    panels.push(
      <details key="escalate" className="rounded border border-border p-3">
        <summary className="cursor-pointer font-medium">Escalate to a lead</summary>
        <div className="mt-3">
          <ActionForm action={escalate.bind(null, kase.id, kase.version)} submit="Escalate" tone="danger">
            <label className="block">
              <span className="text-muted">Reason</span>
              <select name="reason" required className={input}>
                {OPERATOR_ESCALATIONS.map((r) => (
                  <option key={r} value={r}>
                    {humanize(r)}
                  </option>
                ))}
              </select>
            </label>
            <Note label="Note" />
          </ActionForm>
        </div>
      </details>,
    );
  }

  return (
    <Section title="Decision" id="decision" aside={`case version ${kase.version}`}>
      {panels.length > 0 ? (
        <div className="space-y-4">{panels}</div>
      ) : (
        <p className="text-muted">Nothing for you to decide in this state.</p>
      )}
    </Section>
  );
}
