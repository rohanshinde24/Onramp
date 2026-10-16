import { describe, expect, it } from "vitest";

import { describeError } from "./errors";
import { age, incidentBasis, money, passageLabel, percent } from "./format";
import { queueOf } from "./queues";

describe("formatting", () => {
  it("shows integer cents as currency", () => {
    expect(money(8400, "USD")).toBe("$84.00");
    expect(money(5, "USD")).toBe("$0.05");
  });

  it("shows case age for triage", () => {
    const now = new Date("2026-08-23T13:30:00Z");
    expect(age("2026-08-20T09:00:00Z", now)).toBe("3d 4h");
    expect(age("2026-08-23T08:18:00Z", now)).toBe("5h 12m");
    expect(age("2026-08-23T13:22:00Z", now)).toBe("8m");
  });

  it("labels passages by policy, version and section", () => {
    expect(passageLabel("payout_v12_section_4_3")).toBe("payout v12 §4.3");
    expect(passageLabel("quality_review_v6_section_4_1")).toBe("quality review v6 §4.1");
    expect(passageLabel("odd")).toBe("odd");
  });

  it("says where the incident time came from", () => {
    expect(incidentBasis("submission.submitted_at")).toBe("when the work was submitted");
    expect(incidentBasis("something.else")).toBe("something.else");
  });

  it("prints missing rates as n/a", () => {
    expect(percent(0.9891)).toBe("98.9%");
    expect(percent(null)).toBe("n/a");
  });
});

describe("queues", () => {
  it("falls back to the decision queue for unknown input", () => {
    expect(queueOf("approve")).toBe("approve");
    expect(queueOf(["escalated", "x"])).toBe("escalated");
    expect(queueOf("constructor")).toBe("decide");
    expect(queueOf(undefined)).toBe("decide");
  });
});

describe("errors", () => {
  it("explains known API refusals and passes unknown codes through", () => {
    expect(describeError("content_mismatch", null)).toMatch(/proposal changed/);
    expect(describeError("weird_thing", null)).toBe("weird thing");
    expect(describeError("constructor", null)).toBe("constructor");
  });
});
