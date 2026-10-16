import { Badge, Empty, Fields, RefList, Section, Untrusted, anchorFor } from "@/components/ui";
import { dateTime, humanize, incidentBasis, money, passageLabel } from "@/lib/format";
import type {
  Approval,
  CasePolicy,
  CheckRun,
  EvidenceItem,
  ExecutedAction,
  InvestigationRun,
  Passage,
  Proposal,
  TimelineEntry,
} from "@/lib/types";

const OUTCOME_TONE: Record<string, "good" | "attention" | "danger" | "info"> = {
  resolve: "good",
  wait_for_settlement: "info",
  wait_for_evidence: "info",
  escalate: "danger",
  investigate: "attention",
};

export function ChecksSection({ runs }: { runs: CheckRun[] }) {
  return (
    <Section title="Deterministic checks" id="checks" aside={`${runs.length} run(s)`}>
      {runs.length === 0 ? (
        <Empty>No checks have run.</Empty>
      ) : (
        <ol className="space-y-4">
          {[...runs].reverse().map((run) => (
            <li key={run.id} className="space-y-2">
              <div className="flex flex-wrap items-center gap-2">
                <Badge tone={OUTCOME_TONE[run.outcome] ?? "neutral"}>{humanize(run.outcome)}</Badge>
                {run.policy_id && (
                  <span className="text-muted">
                    under {run.policy_id} v{run.policy_version}
                  </span>
                )}
                <span className="ml-auto text-xs text-muted">{dateTime(run.created_at)}</span>
              </div>
              <Fields
                rows={[
                  ["Reasons", run.reason_codes.join(", ") || "none"],
                  ["Citations", <RefList key="c" refs={run.citations} />],
                  ["Evidence", <RefList key="e" refs={run.evidence_refs} />],
                  ...(run.missing_items.length
                    ? ([["Missing", run.missing_items.join(", ")]] as [string, string][])
                    : []),
                  ...(run.wait_until
                    ? ([["Waits until", dateTime(run.wait_until)]] as [string, string][])
                    : []),
                  ...(run.error ? ([["Error", run.error]] as [string, string][]) : []),
                ]}
              />
              {run.findings.length > 0 && (
                <ul className="list-disc pl-5 text-muted">
                  {run.findings.map((f, i) => (
                    <li key={i}>{f}</li>
                  ))}
                </ul>
              )}
            </li>
          ))}
        </ol>
      )}
    </Section>
  );
}

const RUN_TONE: Record<string, "good" | "attention" | "danger" | "neutral"> = {
  concluded: "good",
  budget_exhausted: "attention",
  invalid_output: "danger",
  model_error: "danger",
  model_refused: "danger",
  superseded: "neutral",
};

export function InvestigationSection({ runs }: { runs: InvestigationRun[] }) {
  return (
    <Section title="Investigation" id="investigation" aside={`${runs.length} run(s)`}>
      {runs.length === 0 ? (
        <Empty>The investigator has not run. Checks settled the case or have not finished.</Empty>
      ) : (
        <ol className="space-y-5">
          {[...runs].reverse().map((run) => {
            const c = run.conclusion;
            return (
              <li key={run.id} className="space-y-2">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge tone={RUN_TONE[run.status] ?? "neutral"}>{humanize(run.status)}</Badge>
                  <span className="mono text-xs">
                    {run.model_name} · {run.prompt_version}
                  </span>
                  <span className="ml-auto text-xs text-muted">{dateTime(run.created_at)}</span>
                </div>
                {c && (
                  <Fields
                    rows={[
                      [
                        "Conclusion",
                        <strong key="d">
                          {c.decision === "resolve"
                            ? c.resolution_code
                            : `Escalate: ${humanize(c.escalation_reason ?? "unspecified")}`}
                        </strong>,
                      ],
                      ["Confidence", c.confidence != null ? c.confidence.toFixed(2) : "n/a"],
                      ...(c.proposed_action
                        ? ([
                            [
                              "Proposed action",
                              `${c.proposed_action.action_type}${
                                c.proposed_action.amount_cents
                                  ? ` ${money(c.proposed_action.amount_cents, c.proposed_action.currency ?? "USD")}`
                                  : ""
                              }`,
                            ],
                          ] as [string, string][])
                        : []),
                      ["Reason codes", (c.reason_codes ?? []).join(", ") || "none"],
                      ["Citations", <RefList key="c" refs={c.citations ?? []} />],
                      ["Evidence", <RefList key="e" refs={c.evidence_refs ?? []} />],
                      [
                        "Rationale",
                        <span key="r" className="whitespace-pre-wrap">
                          {c.rationale}
                        </span>,
                      ],
                    ]}
                  />
                )}
                {run.problems.length > 0 && (
                  <div>
                    <p className="text-muted">Rejected by validation:</p>
                    <ul className="list-disc pl-5 text-red-700 dark:text-red-400">
                      {run.problems.map((p, i) => (
                        <li key={i}>{p}</li>
                      ))}
                    </ul>
                  </div>
                )}
                <p className="text-xs text-muted">
                  {run.model_calls} model call(s), {run.input_tokens + run.output_tokens} tokens,{" "}
                  {run.elapsed_s.toFixed(1)}s
                  {run.proposal_id ? ` · became proposal ${run.proposal_id}` : ""}
                </p>
                <details>
                  <summary className="cursor-pointer text-muted">
                    Trajectory ({run.trajectory.length} steps)
                  </summary>
                  <ol className="mt-2 space-y-1 border-l border-border pl-3">
                    {run.trajectory.map((step, i) => (
                      <li key={i} className="text-xs">
                        <span className="text-muted">
                          {step.step}. {step.kind}
                        </span>{" "}
                        {step.kind === "tool" && (
                          <>
                            <code>{step.tool}</code>
                            {step.arguments && Object.keys(step.arguments).length > 0 && (
                              <code className="text-muted"> {JSON.stringify(step.arguments)}</code>
                            )}
                            {step.error && <span className="text-red-700"> {step.error}</span>}
                            {[...(step.refs ?? []), ...(step.passages ?? [])].length > 0 && (
                              <span className="ml-1 inline-flex flex-wrap gap-1">
                                <RefList refs={[...(step.refs ?? []), ...(step.passages ?? [])]} />
                              </span>
                            )}
                          </>
                        )}
                        {step.kind === "model" && (
                          <span>
                            called {(step.tool_calls ?? []).join(", ") || "no tools"} (
                            {(step.input_tokens ?? 0) + (step.output_tokens ?? 0)} tokens)
                          </span>
                        )}
                        {step.kind === "validation" && (
                          <span>
                            {(step.problems ?? []).length === 0
                              ? "conclusion accepted"
                              : `rejected: ${(step.problems ?? []).join("; ")}`}
                          </span>
                        )}
                      </li>
                    ))}
                  </ol>
                </details>
              </li>
            );
          })}
        </ol>
      )}
    </Section>
  );
}

// Fields that hold text someone outside Onramp wrote.
const UNTRUSTED_FIELDS = new Set(["content_text", "reviewer_comment"]);

function value(v: unknown): string {
  if (v === null || v === undefined) return "n/a";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

export function EvidenceSection({ items }: { items: EvidenceItem[] }) {
  return (
    <Section title="Evidence" id="evidence" aside="marketplace records reachable from the case">
      <ul className="space-y-3">
        {items.map((item) => {
          const fields = Object.entries(item.record).filter(
            ([k]) => k !== "id" && !UNTRUSTED_FIELDS.has(k),
          );
          const untrusted = Object.entries(item.record).filter(([k]) => UNTRUSTED_FIELDS.has(k));
          return (
            <li
              key={item.ref}
              id={anchorFor(item.ref)}
              className="scroll-mt-4 rounded border border-border p-2 target:ring-2 target:ring-accent"
            >
              <div className="flex items-center gap-2">
                <code className="text-xs font-semibold">{item.ref}</code>
                <span className="text-xs text-muted">from {item.source}</span>
              </div>
              <dl className="mt-1 grid grid-cols-[max-content_1fr] gap-x-3 text-xs">
                {fields.map(([k, v]) => (
                  <div key={k} className="contents">
                    <dt className="text-muted">{k}</dt>
                    <dd className="mono break-all">
                      {k.endsWith("_cents") && typeof v === "number" && "currency" in item.record
                        ? `${money(v, String(item.record.currency))} (${v})`
                        : value(v)}
                    </dd>
                  </div>
                ))}
              </dl>
              {untrusted.map(([k, v]) => (
                <div key={k} className="mt-2">
                  <Untrusted label={k} text={String(v ?? "")} />
                </div>
              ))}
            </li>
          );
        })}
      </ul>
    </Section>
  );
}

export function PolicySection({
  policy,
  passages,
}: {
  policy: CasePolicy;
  passages: (Passage | { passage_id: string; missing: true })[];
}) {
  const inForce = new Map(policy.versions.map((v) => [v.policy_id, v.version]));
  return (
    <Section title="Policy" id="policy" aside={`in force at ${dateTime(policy.incident_at)}`}>
      <p className="text-muted">
        The incident happened {incidentBasis(policy.incident_basis)}, so these versions govern
        the case:
      </p>
      <ul className="mt-2 space-y-1">
        {policy.versions.map((v) => (
          <li key={v.policy_id}>
            <strong>
              {v.policy_id} v{v.version}
            </strong>{" "}
            <span className="text-muted">
              effective {dateTime(v.effective_from)}
              {v.effective_until ? ` until ${dateTime(v.effective_until)}` : ", still current"}
            </span>
            {Object.keys(v.parameters).length > 0 && (
              <code className="ml-2 text-xs text-muted">{JSON.stringify(v.parameters)}</code>
            )}
          </li>
        ))}
      </ul>
      <h3 className="mt-4 font-medium">Cited passages</h3>
      {passages.length === 0 ? (
        <Empty>Nothing on this case cites a policy passage yet.</Empty>
      ) : (
        <ul className="mt-2 space-y-3">
          {passages.map((p) =>
            "missing" in p ? (
              <li key={p.passage_id} id={anchorFor(p.passage_id)} className="text-red-700">
                <code>{p.passage_id}</code> does not exist in the policy corpus.
              </li>
            ) : (
              <li
                key={p.passage_id}
                id={anchorFor(p.passage_id)}
                className="scroll-mt-4 rounded border border-border p-2 target:ring-2 target:ring-accent"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <strong>{passageLabel(p.passage_id)}</strong>
                  <span>{p.heading}</span>
                  {inForce.has(p.policy_id) && inForce.get(p.policy_id) !== p.version && (
                    <Badge tone="danger">
                      not the version in force (v{inForce.get(p.policy_id)})
                    </Badge>
                  )}
                </div>
                <p className="mt-1 whitespace-pre-wrap">{p.text}</p>
              </li>
            ),
          )}
        </ul>
      )}
    </Section>
  );
}

export function ActionsSection({
  actions,
  approvals,
  proposals,
}: {
  actions: ExecutedAction[];
  approvals: Approval[];
  proposals: Proposal[];
}) {
  const approvalOf = new Map(approvals.map((a) => [a.proposal_id, a]));
  const actionOf = new Map(actions.map((a) => [a.proposal_id, a]));
  return (
    <Section title="Proposals, approvals and actions" id="actions">
      {proposals.length === 0 ? (
        <Empty>No proposals yet.</Empty>
      ) : (
        <ol className="space-y-3">
          {[...proposals].reverse().map((p) => {
            const approval = approvalOf.get(p.id);
            const action = actionOf.get(p.id);
            return (
              <li key={p.id} className="space-y-1 border-b border-border pb-3 last:border-0">
                <div className="flex flex-wrap items-center gap-2">
                  <strong>{p.resolution_code}</strong>
                  {p.action_type && <span className="text-muted">{p.action_type}</span>}
                  <Badge
                    tone={
                      p.status === "accepted" ? "good" : p.status === "rejected" ? "danger" : "attention"
                    }
                  >
                    {p.status}
                  </Badge>
                  <span className="text-xs text-muted">by {p.proposer_id ?? p.proposer_type}</span>
                </div>
                {p.decision_note && <p className="text-muted">Decision note: {p.decision_note}</p>}
                {approval && (
                  <p>
                    {approval.decision === "approved" ? "Approved" : "Rejected"} by{" "}
                    <code>{approval.approver_id}</code> at {dateTime(approval.decided_at)}, hash{" "}
                    <code className="text-xs">{approval.content_hash.slice(0, 12)}</code>
                    {approval.note && <span className="text-muted">: {approval.note}</span>}
                  </p>
                )}
                {action && (
                  <div className="text-xs">
                    <p>
                      Gateway:{" "}
                      <Badge
                        tone={
                          action.status === "succeeded"
                            ? "good"
                            : action.status === "executing"
                              ? "info"
                              : "danger"
                        }
                      >
                        {action.status}
                      </Badge>{" "}
                      idempotency key <code>{action.idempotency_key}</code>
                    </p>
                    <p className="mono text-muted">request {JSON.stringify(action.request)}</p>
                    {action.result && (
                      <p className="mono text-muted">result {JSON.stringify(action.result)}</p>
                    )}
                    {action.failure_code && (
                      <p className="text-red-700 dark:text-red-400">
                        {action.failure_code}: {action.failure_message}
                      </p>
                    )}
                  </div>
                )}
              </li>
            );
          })}
        </ol>
      )}
    </Section>
  );
}

function describe(entry: TimelineEntry): string {
  const d = entry.details;
  if (entry.action === "case.transitioned") {
    return `${d.from} → ${d.to}${d.reason ? ` (${humanize(String(d.reason)).toLowerCase()})` : ""}`;
  }
  return humanize(entry.action);
}

export function TimelineSection({ entries }: { entries: TimelineEntry[] }) {
  return (
    <Section title="Audit timeline" id="timeline" aside={`${entries.length} event(s)`}>
      <ol className="space-y-2 border-l border-border pl-3">
        {entries.map((e) => {
          const extra = Object.entries(e.details).filter(
            ([k]) => !(e.action === "case.transitioned" && ["from", "to", "reason"].includes(k)),
          );
          return (
            <li key={e.event_id}>
              <div className="text-xs text-muted">
                {dateTime(e.occurred_at)} · {e.actor_type}
                {e.actor_id ? ` ${e.actor_id}` : ""}
              </div>
              <div>{describe(e)}</div>
              {extra.length > 0 && (
                <div className="mono break-all text-xs text-muted">
                  {extra.map(([k, v]) => `${k}=${value(v)}`).join("  ")}
                </div>
              )}
            </li>
          );
        })}
      </ol>
    </Section>
  );
}
