import { describe, expect, it } from "vitest";

import { isSafeName, outcomeLabel, parseResults, parseRun } from "./eval-runs";

describe("evaluation runs", () => {
  it("refuses directory names that could leave the artifact root", () => {
    expect(isSafeName("20261014T174314Z")).toBe(true);
    expect(isSafeName("sonnet-5-5-v2")).toBe(true);
    expect(isSafeName("..")).toBe(false);
    expect(isSafeName("../secrets")).toBe(false);
    expect(isSafeName("a/b")).toBe(false);
    expect(isSafeName(".hidden")).toBe(false);
  });

  it("tells live-model runs from scripted ones", () => {
    const summary = { scenarios: 92, status: { success: 92 }, graded: null, cost_usd: 1.1 };
    const live = parseRun("judgment-eval", "r", "run", { model: "claude-sonnet-5-5@medium" }, summary);
    const scripted = parseRun("case-eval", "r", "run", { model: "scripted:reference:v2" }, summary);

    expect(live.live).toBe(true);
    expect(scripted.live).toBe(false);
    expect(live.costUsd).toBe(1.1);
  });

  it("reads graded rows and run failures from results.jsonl", () => {
    const jsonl = [
      JSON.stringify({
        scenario_id: "a",
        status: "success",
        final_state: "escalated",
        faults: ["marketplace:lost_response@1"],
        grade: { expected_resolution: "UNDERPAID", actual_resolution: "__escalated__", outcome_correct: false },
      }),
      JSON.stringify({ scenario_id: "b", status: "harness_error", error: "setup", grade: null }),
      "",
    ].join("\n");

    const [a, b] = parseResults(jsonl);

    expect(a.outcomeCorrect).toBe(false);
    expect(outcomeLabel(a.actual)).toBe("escalated");
    expect(a.faults).toEqual(["marketplace:lost_response@1"]);
    expect(b.status).toBe("harness_error");
    expect(b.outcomeCorrect).toBeNull();
  });
});
