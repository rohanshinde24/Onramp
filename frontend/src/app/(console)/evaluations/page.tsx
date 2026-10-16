import type { Metadata } from "next";
import Link from "next/link";

import { Badge, Empty } from "@/components/ui";
import { SUITES, SUITE_LABEL, type RunSummary } from "@/lib/eval-runs";
import { listRuns } from "@/lib/eval-store";
import { getMe } from "@/lib/me";
import { percent } from "@/lib/format";

export const metadata: Metadata = { title: "Evaluations" };

function failures(run: RunSummary): number {
  return (run.status.app_failure ?? 0) + (run.status.harness_error ?? 0);
}

export default async function EvaluationsPage() {
  await getMe(); // signed-in users only
  const runs = await listRuns();

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-lg font-semibold">Evaluations</h1>
        <p className="mt-1 max-w-3xl text-muted">
          Runs of the whole application against labelled scenarios, read from the run artifacts
          on this machine. Baselines are committed; other runs are local. Scripted runs measure
          the application around the model; live runs measure a model&apos;s judgment.
          Published numbers, with their commands and limitations, are in docs/BENCHMARKS.md.
        </p>
      </div>
      {SUITES.map((suite) => {
        const rows = runs.filter((r) => r.suite === suite);
        return (
          <section key={suite} className="space-y-2">
            <h2 className="font-semibold">{SUITE_LABEL[suite]}</h2>
            {rows.length === 0 ? (
              <Empty>No runs found for {suite}.</Empty>
            ) : (
              <div className="overflow-x-auto rounded-md border border-border bg-surface">
                <table className="w-full border-collapse text-left">
                  <thead className="text-muted">
                    <tr className="border-b border-border">
                      <th className="px-3 py-2 font-medium">Run</th>
                      <th className="px-3 py-2 font-medium">Model · prompt</th>
                      <th className="px-3 py-2 font-medium">Commit</th>
                      <th className="px-3 py-2 text-right font-medium">Cases</th>
                      <th className="px-3 py-2 text-right font-medium">Outcome acc.</th>
                      <th className="px-3 py-2 text-right font-medium">Esc. P / R</th>
                      <th className="px-3 py-2 text-right font-medium">Cite P / R</th>
                      <th className="px-3 py-2 text-right font-medium">Safety</th>
                      <th className="px-3 py-2 text-right font-medium">Cost</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((r) => {
                      const g = r.graded;
                      return (
                        <tr key={`${r.kind}/${r.name}`} className="border-b border-border last:border-0">
                          <td className="px-3 py-2">
                            <Link
                              href={`/evaluations/${suite}/${r.kind}/${r.name}`}
                              className="mono text-xs text-accent hover:underline"
                            >
                              {r.name}
                            </Link>{" "}
                            {r.kind === "baseline" && <Badge tone="info">baseline</Badge>}{" "}
                            {failures(r) > 0 && <Badge tone="danger">{failures(r)} failed</Badge>}
                            {r.stopped && <Badge tone="attention">stopped</Badge>}
                          </td>
                          <td className="mono px-3 py-2 text-xs">
                            {r.model}
                            {r.promptVersion ? ` · ${r.promptVersion}` : ""}
                          </td>
                          <td className="mono px-3 py-2 text-xs">
                            {r.commit || "n/a"}
                            {r.dirty ? " (dirty)" : ""}
                          </td>
                          <td className="px-3 py-2 text-right tabular-nums">{r.scenarios}</td>
                          <td className="px-3 py-2 text-right tabular-nums">
                            {percent(g?.outcome_accuracy)}
                          </td>
                          <td className="px-3 py-2 text-right tabular-nums">
                            {percent(g?.escalation_precision, 0)} / {percent(g?.escalation_recall, 0)}
                          </td>
                          <td className="px-3 py-2 text-right tabular-nums">
                            {percent(g?.citation_precision, 0)} / {percent(g?.citation_recall, 0)}
                          </td>
                          <td className="px-3 py-2 text-right tabular-nums">
                            {g ? g.safety_violations : "n/a"}
                          </td>
                          <td className="px-3 py-2 text-right tabular-nums">
                            {r.costUsd ? `$${r.costUsd.toFixed(2)}` : "n/a"}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        );
      })}
    </div>
  );
}
