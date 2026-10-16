import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { Badge, Fields, Section } from "@/components/ui";
import { SUITE_LABEL, isSuite, outcomeLabel } from "@/lib/eval-runs";
import { getRun } from "@/lib/eval-store";
import { dateTime, percent } from "@/lib/format";
import { getMe } from "@/lib/me";

type Props = PageProps<"/evaluations/[suite]/[kind]/[name]">;

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  return { title: `Run ${(await params).name}` };
}

export default async function RunPage({ params }: Props) {
  await getMe();
  const { suite, kind, name } = await params;
  if (!isSuite(suite) || (kind !== "run" && kind !== "baseline")) notFound();
  const loaded = await getRun(kind, suite, name);
  if (!loaded) notFound();
  const { run, results } = loaded;
  const g = run.graded;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <Link href="/evaluations" className="text-muted hover:text-foreground">
          Evaluations
        </Link>
        <span className="text-muted">/</span>
        <h1 className="mono text-base font-semibold">{run.name}</h1>
        <span className="text-muted">{SUITE_LABEL[suite]}</span>
        {run.kind === "baseline" && <Badge tone="info">baseline</Badge>}
        <Badge tone={run.live ? "attention" : "neutral"}>{run.live ? "live model" : "scripted"}</Badge>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Section title="Run">
          <Fields
            rows={[
              ["Run ID", <code key="r">{run.runId}</code>],
              ["Model", <code key="m">{run.model}</code>],
              ["Prompt", run.promptVersion ?? "n/a"],
              ["Commit", `${run.commit || "n/a"}${run.dirty ? " (dirty tree)" : ""}`],
              ["Started", run.startedAt ? dateTime(run.startedAt) : "n/a"],
              [
                "Status",
                Object.entries(run.status)
                  .map(([k, v]) => `${k} ${v}`)
                  .join(", ") || "n/a",
              ],
              ...(run.costUsd != null
                ? ([
                    [
                      "Cost",
                      `$${run.costUsd.toFixed(4)}${
                        run.costPerCorrectUsd != null
                          ? `, $${run.costPerCorrectUsd.toFixed(4)} per correct outcome`
                          : ""
                      }`,
                    ],
                  ] as [string, string][])
                : []),
              ...(run.stopped ? ([["Stopped", run.stopped]] as [string, string][]) : []),
            ]}
          />
        </Section>
        <Section title="Graded">
          {g ? (
            <Fields
              rows={[
                ["Outcome accuracy", `${percent(g.outcome_accuracy)} of ${g.cases}`],
                ...Object.entries(g.outcome_accuracy_by_case_type ?? {}).map(
                  ([t, v]) => [`  ${t}`, percent(v)] as [string, string],
                ),
                ["Resolution macro F1", percent(g.resolution_macro_f1)],
                [
                  "Escalation P / R",
                  `${percent(g.escalation_precision)} / ${percent(g.escalation_recall)} (over ${g.over_escalations ?? 0}, under ${g.under_escalations ?? 0})`,
                ],
                [
                  "Citation P / R",
                  `${percent(g.citation_precision)} / ${percent(g.citation_recall)}, ${g.fabricated_citations ?? 0} fabricated`,
                ],
                ["Safety violations", String(g.safety_violations)],
                ["Settled without a model", percent(g.deterministic_fast_path_rate)],
                ["Mean model calls", g.mean_model_calls?.toFixed(2) ?? "n/a"],
              ]}
            />
          ) : (
            <p className="text-muted">No graded cases: every scenario failed to run.</p>
          )}
        </Section>
      </div>

      {run.limitations.length > 0 && (
        <Section title="Recorded limitations">
          {run.live && run.limitations[0]?.startsWith("Investigations are scripted") && (
            <p className="mb-2 text-red-700 dark:text-red-400">
              This live-model run was recorded before manifests told live and scripted runs
              apart, so its first limitation wrongly says investigations are scripted.
            </p>
          )}
          <ul className="list-disc space-y-1 pl-5 text-muted">
            {run.limitations.map((l, i) => (
              <li key={i}>{l}</li>
            ))}
          </ul>
        </Section>
      )}

      <Section title="Scenarios" aside={`${results.length} result(s)`}>
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-left text-xs">
            <thead className="text-muted">
              <tr className="border-b border-border">
                <th className="py-1.5 pr-3 font-medium">Scenario</th>
                <th className="py-1.5 pr-3 font-medium">Type</th>
                <th className="py-1.5 pr-3 font-medium">Expected</th>
                <th className="py-1.5 pr-3 font-medium">Actual</th>
                <th className="py-1.5 pr-3 font-medium">Final state</th>
                <th className="py-1.5 pr-3 text-right font-medium">Cite P / R</th>
                <th className="py-1.5 pr-3 font-medium">Notes</th>
              </tr>
            </thead>
            <tbody>
              {results.map((r) => (
                <tr
                  key={r.scenarioId}
                  className={`border-b border-border last:border-0 ${
                    r.outcomeCorrect === false || r.status !== "success"
                      ? "bg-red-50 dark:bg-red-950/30"
                      : ""
                  }`}
                >
                  <td className="mono py-1.5 pr-3">{r.scenarioId}</td>
                  <td className="py-1.5 pr-3">{r.caseType ?? "n/a"}</td>
                  <td className="py-1.5 pr-3">{outcomeLabel(r.expected)}</td>
                  <td className="py-1.5 pr-3">
                    {outcomeLabel(r.actual)}{" "}
                    {r.outcomeCorrect === false && <Badge tone="danger">wrong</Badge>}
                  </td>
                  <td className="py-1.5 pr-3">{r.finalState ?? "n/a"}</td>
                  <td className="py-1.5 pr-3 text-right tabular-nums">
                    {percent(r.citationPrecision, 0)} / {percent(r.citationRecall, 0)}
                  </td>
                  <td className="py-1.5 pr-3 text-muted">
                    {[
                      r.status !== "success" ? `${r.status}: ${r.error ?? ""}` : null,
                      r.safetyViolations.length ? `safety: ${r.safetyViolations.join(", ")}` : null,
                      r.faults.length ? `faults: ${r.faults.join(", ")}` : null,
                    ]
                      .filter(Boolean)
                      .join(" · ") || ""}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Section>
    </div>
  );
}
