import { execFileSync } from "node:child_process";
import path from "node:path";

import { type Page, expect } from "@playwright/test";

import type { Case, CasePage, Proposal } from "../src/lib/types";

// The e2e API, never the dev one: tests write to whatever this points at.
export const API_URL = process.env.ONRAMP_E2E_API_URL ?? "http://127.0.0.1:8100";
const REPO = path.resolve(__dirname, "../..");

/** A bearer token minted the way a person signing in locally would, with the repo's .env. */
export function devToken(tenant: string, user: string): string {
  return execFileSync(path.join(REPO, "backend/.venv/bin/onramp-dev-token"), [tenant, user], {
    cwd: REPO,
    encoding: "utf8",
  }).trim();
}

/** Call the API directly, for setup and for checking what the console did or did not write. */
export async function api(
  token: string,
  route: string,
  init: { method?: string; body?: unknown } = {},
): Promise<Response> {
  return fetch(`${API_URL}${route}`, {
    method: init.method ?? "GET",
    headers: {
      Authorization: `Bearer ${token}`,
      Accept: "application/json",
      ...(init.body !== undefined ? { "Content-Type": "application/json" } : {}),
    },
    body: init.body !== undefined ? JSON.stringify(init.body) : undefined,
  });
}

export async function apiJson<T>(token: string, route: string): Promise<T> {
  const response = await api(token, route);
  expect(response.status, `GET ${route}`).toBe(200);
  return (await response.json()) as T;
}

/**
 * The seeded case about this subject, once the e2e workflow worker has run its checks into the
 * expected state. A different state means the world was used already: rerun `make e2e`.
 */
export async function seededCase(token: string, subject: string, state: string): Promise<Case> {
  let found: Case | undefined;
  await expect
    .poll(
      async () => {
        const page = await apiJson<CasePage>(token, "/cases?limit=100");
        found = page.cases.find((c) => c.subject === subject);
        return found?.state;
      },
      { message: `seeded case for ${subject} (rerun make e2e for a fresh world)`, timeout: 30_000 },
    )
    .toBe(state);
  return found!;
}

export async function proposalsOf(token: string, caseId: string): Promise<Proposal[]> {
  return apiJson<Proposal[]>(token, `/cases/${caseId}/proposals`);
}

export async function signIn(page: Page, token: string): Promise<void> {
  await page.goto("/login");
  await page.getByLabel("Bearer token").fill(token);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/cases$/);
}

export async function signOut(page: Page): Promise<void> {
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page).toHaveURL(/\/login$/);
}

/** The state badge next to the case ID in the case page's heading. */
export function stateBadge(page: Page) {
  return page.getByRole("heading", { level: 1 }).locator("xpath=following-sibling::*[1]");
}
