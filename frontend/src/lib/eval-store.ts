import "server-only";

import { readdir, readFile } from "node:fs/promises";
import path from "node:path";

import {
  type RunSummary,
  type ScenarioResult,
  type Suite,
  SUITES,
  isSafeName,
  parseResults,
  parseRun,
} from "./eval-runs";

// Evaluation artifacts are files (ADR-009), not application state: the API never serves them
// and the console's server reads them read-only. Committed baselines live under
// eval/baselines; local runs under artifacts/, which is git-ignored and may be absent.
const ROOT = path.resolve(process.env.ONRAMP_EVAL_ROOT ?? path.join(process.cwd(), ".."));

function dirFor(kind: RunSummary["kind"], suite: Suite): string {
  return kind === "baseline"
    ? path.join(ROOT, "eval", "baselines", suite)
    : path.join(ROOT, "artifacts", suite);
}

async function readJson(file: string): Promise<Record<string, unknown> | null> {
  try {
    return JSON.parse(await readFile(file, "utf8")) as Record<string, unknown>;
  } catch {
    return null;
  }
}

async function names(dir: string): Promise<string[]> {
  try {
    const entries = await readdir(dir, { withFileTypes: true });
    return entries.filter((e) => e.isDirectory() && isSafeName(e.name)).map((e) => e.name);
  } catch {
    return [];
  }
}

async function load(
  kind: RunSummary["kind"],
  suite: Suite,
  name: string,
): Promise<RunSummary | null> {
  if (!isSafeName(name)) return null;
  const dir = path.join(dirFor(kind, suite), name);
  const [manifest, summary] = await Promise.all([
    readJson(path.join(dir, "manifest.json")),
    readJson(path.join(dir, "summary.json")),
  ]);
  // A run that crashed before writing its summary has nothing to show.
  return manifest && summary ? parseRun(suite, name, kind, manifest, summary) : null;
}

export async function listRuns(): Promise<RunSummary[]> {
  const found = await Promise.all(
    SUITES.flatMap((suite) =>
      (["baseline", "run"] as const).map(async (kind) =>
        Promise.all((await names(dirFor(kind, suite))).map((n) => load(kind, suite, n))),
      ),
    ),
  );
  return found
    .flat()
    .filter((r): r is RunSummary => r !== null)
    .sort((a, b) => (b.startedAt ?? "").localeCompare(a.startedAt ?? ""));
}

export async function getRun(
  kind: RunSummary["kind"],
  suite: Suite,
  name: string,
): Promise<{ run: RunSummary; results: ScenarioResult[] } | null> {
  const run = await load(kind, suite, name);
  if (!run) return null;
  try {
    const jsonl = await readFile(path.join(dirFor(kind, suite), name, "results.jsonl"), "utf8");
    return { run, results: parseResults(jsonl) };
  } catch {
    return { run, results: [] };
  }
}
