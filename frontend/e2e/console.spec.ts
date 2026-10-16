import { expect, test } from "@playwright/test";

import {
  api,
  apiJson,
  devToken,
  proposalsOf,
  seededCase,
  signIn,
  signOut,
  stateBadge,
} from "./support";
import type { Approval, CasePage, ExecutedAction } from "../src/lib/types";

// One seeded world (backend onramp-seed) that `make e2e` recreates before each run. The e2e
// workflow worker has already run each case's checks: acme's s_8821 payout failed to settle and
// has a high-risk PAYOUT_ADJUSTMENT proposal; s_8822 settled and has a PAID_IN_FULL proposal.

const operator = devToken("acme", "acme_operator");
const lead = devToken("acme", "acme_lead");

test("an operator accepts, only a lead approves, and the gateway resolves the case", async ({
  page,
}) => {
  const kase = await seededCase(operator, "submission:acme_s_8821", "proposed");
  const [proposal] = await proposalsOf(operator, kase.id);
  expect(proposal).toMatchObject({ action_type: "PAYOUT_ADJUSTMENT", risk_class: "high" });

  // The operator finds the case in the inbox's default queue and accepts the proposal.
  await signIn(page, operator);
  await page.getByRole("link", { name: kase.id }).click();
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(kase.id);
  await expect(stateBadge(page)).toHaveText("Needs decision");
  await page.getByRole("button", { name: "Accept and send for approval" }).click();

  // The console offers the operator no approval control for money movement...
  await expect(stateBadge(page)).toHaveText("Awaiting approval");
  await expect(page.getByText("Awaiting approval. A lead approves money movement.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Record decision" })).toHaveCount(0);
  // ...and the API refuses it regardless, because hiding a control is not enforcement.
  const refused = await api(operator, `/proposals/${proposal.id}/approval`, {
    method: "POST",
    body: { decision: "approved", content_hash: proposal.content_hash, note: "" },
  });
  expect(refused.status).toBe(403);
  expect(await refused.json()).toMatchObject({ error: "forbidden" });
  await signOut(page);

  // The lead approves the exact proposal the page rendered.
  await signIn(page, lead);
  await page.goto(`/cases/${kase.id}`);
  await expect(page.getByText(`Your decision is bound to content hash`)).toContainText(
    proposal.content_hash.slice(0, 12),
  );
  await page.getByLabel("Approve", { exact: true }).check();
  await page.getByRole("button", { name: "Record decision" }).click();

  const actions = page.locator("#actions");
  await expect(actions).toContainText("Approved by acme_lead");
  await expect(actions).toContainText(proposal.content_hash.slice(0, 12));
  const [approval] = await apiJson<Approval[]>(lead, `/cases/${kase.id}/approvals`);
  expect(approval).toMatchObject({
    decision: "approved",
    approver_id: "acme_lead",
    content_hash: proposal.content_hash,
  });

  // Temporal hands the approved proposal to the Action Gateway, which executes and verifies it.
  await expect(async () => {
    await page.reload();
    await expect(stateBadge(page)).toHaveText("Resolved", { timeout: 1_000 });
  }).toPass({ timeout: 60_000 });
  await expect(actions).toContainText("succeeded");
  const [executed] = await apiJson<ExecutedAction[]>(lead, `/cases/${kase.id}/actions`);
  expect(executed).toMatchObject({ proposal_id: proposal.id, status: "succeeded" });
});

test("a write from a stale page is refused and writes no proposal", async ({ context }) => {
  const kase = await seededCase(operator, "submission:acme_s_8822", "proposed");
  const escalated = await api(operator, `/cases/${kase.id}/escalate`, {
    method: "POST",
    body: { reason: "operator_judgment", expected_version: kase.version, note: "e2e" },
  });
  expect(escalated.status).toBe(200);
  const before = await proposalsOf(lead, kase.id);

  // The lead opens the escalated case and starts a manual proposal...
  const stale = await context.newPage();
  await signIn(stale, lead);
  await stale.goto(`/cases/${kase.id}`);
  await expect(stateBadge(stale)).toHaveText("Escalated");
  await stale.getByText("Propose a resolution manually").click();
  await stale.getByLabel("Resolution").selectOption("PAID_IN_FULL");
  await stale.getByRole("checkbox").first().check();
  await stale.getByLabel("Rationale").fill("Settled in full; written from a stale page.");

  // ...while the case is closed from another tab, which moves its version on.
  const other = await context.newPage();
  await other.goto(`/cases/${kase.id}`);
  await other.getByText("Close without action").click();
  await other.getByLabel("Why (duplicate, withdrawn, ...)").fill("Duplicate of another case.");
  await other.getByRole("button", { name: "Close case" }).click();
  await expect(stateBadge(other)).toHaveText("Closed, no action");

  await stale.getByRole("button", { name: "Propose", exact: true }).click();
  await expect(stale.locator("#decision").getByRole("alert")).toHaveText(
    "The case changed since you opened it. Reload to see its current state.",
  );
  expect(await proposalsOf(lead, kase.id)).toEqual(before);
  await stale.reload();
  await expect(stateBadge(stale)).toHaveText("Closed, no action");
});

test("another tenant's case renders as not found", async ({ page }) => {
  const globex = devToken("globex", "globex_operator");
  const { cases } = await apiJson<CasePage>(globex, "/cases?limit=100");
  expect(cases.length).toBeGreaterThan(0);
  const foreign = cases[0].id;

  expect((await api(operator, `/cases/${foreign}`)).status).toBe(404);
  await signIn(page, operator);
  await page.goto(`/cases/${foreign}`);
  await expect(page.getByRole("heading", { name: "Not found" })).toBeVisible();
  await expect(page.getByText(foreign)).toHaveCount(0);
});
