import type { Metadata } from "next";
import Link from "next/link";

import { Fields, Section, StateBadge, Untrusted } from "@/components/ui";
import { get, getOptional } from "@/lib/api";
import { controlsFor } from "@/lib/controls";
import { TYPE_LABEL, dateTime, humanize, incidentBasis } from "@/lib/format";
import { getMe } from "@/lib/me";
import type {
  Approval,
  Case,
  CasePolicy,
  CheckRun,
  EvidenceItem,
  ExecutedAction,
  InvestigationRun,
  Passage,
  Proposal,
  TimelineEntry,
} from "@/lib/types";

import { DecisionPanel } from "./decision";
import {
  ActionsSection,
  ChecksSection,
  EvidenceSection,
  InvestigationSection,
  PolicySection,
  TimelineSection,
} from "./sections";

export async function generateMetadata({ params }: PageProps<"/cases/[caseId]">): Promise<Metadata> {
  return { title: (await params).caseId };
}

/** Every passage anything on the case cites, in first-cited order. */
function citedPassages(
  checks: CheckRun[],
  investigations: InvestigationRun[],
  proposals: Proposal[],
): string[] {
  const ids = [
    ...checks.flatMap((c) => c.citations),
    ...investigations.flatMap((r) => r.conclusion?.citations ?? []),
    ...proposals.flatMap((p) =>
      p.evidence_refs
        .filter((r) => r.startsWith("policy_passage:"))
        .map((r) => r.slice("policy_passage:".length)),
    ),
  ];
  return [...new Set(ids)];
}

export default async function CasePage({ params }: PageProps<"/cases/[caseId]">) {
  const { caseId } = await params;
  const base = `/cases/${encodeURIComponent(caseId)}`;
  // The case read comes first: another tenant's case is a 404 before anything else is asked.
  const kase = await get<Case>(base);
  const [me, timeline, proposals, approvals, checks, investigations, actions, evidence, policy] =
    await Promise.all([
      getMe(),
      get<TimelineEntry[]>(`${base}/timeline`),
      get<Proposal[]>(`${base}/proposals`),
      get<Approval[]>(`${base}/approvals`),
      get<CheckRun[]>(`${base}/checks`),
      get<InvestigationRun[]>(`${base}/investigations`),
      get<ExecutedAction[]>(`${base}/actions`),
      get<EvidenceItem[]>(`${base}/evidence`),
      get<CasePolicy>(`${base}/policy`),
    ]);
  const cited = citedPassages(checks, investigations, proposals);
  const passages = await Promise.all(
    cited.map(
      async (id) =>
        (await getOptional<Passage>(`/policy-passages/${encodeURIComponent(id)}`)) ?? {
          passage_id: id,
          missing: true as const,
        },
    ),
  );
  const controls = controlsFor(kase, me, proposals, approvals);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <Link href="/cases" className="text-muted hover:text-foreground">
          Cases
        </Link>
        <span className="text-muted">/</span>
        <h1 className="mono text-base font-semibold">{kase.id}</h1>
        <StateBadge state={kase.state} />
        <span className="text-muted">{TYPE_LABEL[kase.case_type]}</span>
      </div>

      <div className="grid gap-4 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <div className="space-y-4">
          <Section title="Case">
            <div className="space-y-3">
              <Fields
                rows={[
                  ["Subject", <code key="s">{kase.subject}</code>],
                  ["Worker", <code key="w">{kase.worker_id}</code>],
                  [
                    "Incident",
                    `${dateTime(kase.incident_at)}, ${incidentBasis(kase.incident_basis)}`,
                  ],
                  ["Opened", dateTime(kase.created_at)],
                  ...(kase.resolution_code
                    ? ([["Resolution", kase.resolution_code]] as [string, string][])
                    : []),
                  ...(kase.escalation_reason
                    ? ([["Escalated", humanize(kase.escalation_reason)]] as [string, string][])
                    : []),
                ]}
              />
              <Untrusted label="Reporter's description" text={kase.reported_description} />
            </div>
          </Section>
          <DecisionPanel
            kase={kase}
            me={me}
            controls={controls}
            evidence={evidence}
            citedPassages={cited}
          />
          <InvestigationSection runs={investigations} />
          <ChecksSection runs={checks} />
          <PolicySection policy={policy} passages={passages} />
          <EvidenceSection items={evidence} />
        </div>
        <div className="space-y-4">
          <ActionsSection actions={actions} approvals={approvals} proposals={proposals} />
          <TimelineSection entries={timeline} />
        </div>
      </div>
    </div>
  );
}
