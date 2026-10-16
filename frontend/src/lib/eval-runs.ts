// Parsing for evaluation run artifacts (ADR-009). Pure functions; the file reads live in
// eval-store.ts so these can be tested without a filesystem.

export const SUITES = ["case-eval", "adversarial-eval", "fault-eval", "judgment-eval"] as const;
export type Suite = (typeof SUITES)[number];

export const SUITE_LABEL: Record<Suite, string> = {
  "case-eval": "Reference scenarios",
  "adversarial-eval": "Adversarial",
  "fault-eval": "Fault injection",
  "judgment-eval": "Judgment (live models)",
};

const SAFE_NAME = /^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$/;

export function isSuite(value: string): value is Suite {
  return (SUITES as readonly string[]).includes(value);
}

/** A run or baseline directory name from a URL; anything else could escape the artifact root. */
export function isSafeName(value: string): boolean {
  return SAFE_NAME.test(value) && !value.includes("..");
}

export interface Graded {
  cases: number;
  outcome_accuracy: number;
  macro_case_type_accuracy?: number;
  resolution_macro_f1?: number;
  escalation_precision?: number | null;
  escalation_recall?: number | null;
  over_escalations?: number;
  under_escalations?: number;
  citation_precision?: number | null;
  citation_recall?: number | null;
  fabricated_citations?: number;
  safety_violations: number;
  deterministic_fast_path_rate?: number;
  mean_model_calls?: number;
  mean_total_tokens?: number;
  outcome_accuracy_by_case_type?: Record<string, number>;
}

export interface RunSummary {
  suite: Suite;
  name: string;
  kind: "run" | "baseline";
  runId: string;
  model: string;
  live: boolean;
  promptVersion: string | null;
  commit: string;
  dirty: boolean;
  startedAt: string | null;
  scenarios: number;
  status: Record<string, number>;
  graded: Graded | null;
  wrongOutcomes: string[];
  safetyViolations: Record<string, unknown>;
  costUsd: number | null;
  costPerCorrectUsd: number | null;
  stopped: string | null;
  limitations: string[];
}

type Json = Record<string, unknown>;

const str = (v: unknown, fallback = ""): string => (typeof v === "string" ? v : fallback);
const num = (v: unknown): number | null => (typeof v === "number" ? v : null);

export function parseRun(
  suite: Suite,
  name: string,
  kind: RunSummary["kind"],
  manifest: Json,
  summary: Json,
): RunSummary {
  const model = str(manifest.model, "unknown");
  return {
    suite,
    name,
    kind,
    runId: str(manifest.run_id, name),
    model,
    live: !model.startsWith("scripted"),
    promptVersion: typeof manifest.prompt_version === "string" ? manifest.prompt_version : null,
    commit: str(manifest.git_commit).slice(0, 7),
    dirty: manifest.git_dirty === true,
    startedAt: typeof manifest.started_at === "string" ? manifest.started_at : null,
    scenarios: num(summary.scenarios) ?? num(manifest.scenarios) ?? 0,
    status: (summary.status as Record<string, number>) ?? {},
    graded: (summary.graded as Graded | null) ?? null,
    wrongOutcomes: Array.isArray(summary.wrong_outcomes)
      ? (summary.wrong_outcomes as string[])
      : [],
    safetyViolations: (summary.safety_violations as Record<string, unknown>) ?? {},
    costUsd: num(summary.cost_usd),
    costPerCorrectUsd: num(summary.cost_per_correct_outcome_usd),
    stopped: typeof summary.stopped === "string" ? summary.stopped : null,
    limitations: Array.isArray(manifest.limitations) ? (manifest.limitations as string[]) : [],
  };
}

export interface ScenarioResult {
  scenarioId: string;
  status: string;
  error: string | null;
  finalState: string | null;
  caseType: string | null;
  expected: string | null;
  actual: string | null;
  outcomeCorrect: boolean | null;
  escalationExpected: boolean | null;
  escalated: boolean | null;
  citationPrecision: number | null;
  citationRecall: number | null;
  safetyViolations: string[];
  modelCalls: number | null;
  totalTokens: number | null;
  costUsd: number | null;
  faults: string[];
  steps: string[];
}

export function parseResults(jsonl: string): ScenarioResult[] {
  return jsonl
    .split("\n")
    .filter((line) => line.trim())
    .map((line) => {
      const row = JSON.parse(line) as Json;
      const grade = (row.grade as Json | null) ?? {};
      return {
        scenarioId: str(row.scenario_id),
        status: str(row.status),
        error: typeof row.error === "string" ? row.error : null,
        finalState: typeof row.final_state === "string" ? row.final_state : null,
        caseType: typeof grade.case_type === "string" ? grade.case_type : null,
        expected: typeof grade.expected_resolution === "string" ? grade.expected_resolution : null,
        actual: typeof grade.actual_resolution === "string" ? grade.actual_resolution : null,
        outcomeCorrect: typeof grade.outcome_correct === "boolean" ? grade.outcome_correct : null,
        escalationExpected:
          typeof grade.escalation_expected === "boolean" ? grade.escalation_expected : null,
        escalated: typeof grade.escalated === "boolean" ? grade.escalated : null,
        citationPrecision: num(grade.citation_precision),
        citationRecall: num(grade.citation_recall),
        safetyViolations: Array.isArray(grade.safety_violations)
          ? (grade.safety_violations as string[])
          : [],
        modelCalls: num(grade.model_calls),
        totalTokens: num(grade.total_tokens),
        costUsd: num(row.cost_usd),
        faults: Array.isArray(row.faults) ? (row.faults as unknown[]).map(String) : [],
        steps: Array.isArray(row.steps) ? (row.steps as string[]) : [],
      };
    });
}

// The grader's sentinels for outcomes that are not resolutions.
const SENTINELS: Record<string, string> = {
  __escalated__: "escalated",
  __intake_refused__: "intake refused",
  __none__: "no outcome",
};

export function outcomeLabel(code: string | null): string {
  if (code === null) return "n/a";
  return Object.hasOwn(SENTINELS, code) ? SENTINELS[code] : code;
}
