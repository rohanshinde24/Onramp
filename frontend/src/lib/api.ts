import "server-only";

import { notFound, redirect } from "next/navigation";

import { readToken } from "./session";
import type { ApiErrorBody } from "./types";

const BASE_URL = process.env.ONRAMP_API_URL ?? "http://127.0.0.1:8000";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    readonly detail: string | null,
  ) {
    super(`${status} ${code}${detail ? `: ${detail}` : ""}`);
  }
}

async function call(path: string, init: RequestInit, token: string): Promise<Response> {
  return fetch(`${BASE_URL}${path}`, {
    ...init,
    // Case state is authoritative and changes underneath the console; never serve it stale.
    cache: "no-store",
    headers: {
      ...init.headers,
      Accept: "application/json",
      Authorization: `Bearer ${token}`,
    },
  });
}

async function errorOf(response: Response): Promise<ApiError> {
  const body = (await response.json().catch(() => null)) as Partial<ApiErrorBody> | null;
  // FastAPI's own validation errors use {detail: [...]} rather than our {error, detail}.
  const code = typeof body?.error === "string" ? body.error : `http_${response.status}`;
  const detail =
    typeof body?.detail === "string"
      ? body.detail
      : body?.detail
        ? JSON.stringify(body.detail)
        : null;
  return new ApiError(response.status, code, detail);
}

/**
 * Read for rendering a page. Signed-out and expired sessions go to the sign-in page, and a
 * case outside the caller's tenant renders as not found, exactly as one that does not exist.
 */
export async function get<T>(path: string): Promise<T> {
  const token = await readToken();
  if (!token) redirect("/login");
  const response = await call(path, { method: "GET" }, token);
  if (response.status === 401) redirect("/login");
  if (response.status === 404) notFound();
  if (!response.ok) throw await errorOf(response);
  return (await response.json()) as T;
}

/** Like get, but a 404 is a value rather than a not-found page. */
export async function getOptional<T>(path: string): Promise<T | null> {
  const token = await readToken();
  if (!token) redirect("/login");
  const response = await call(path, { method: "GET" }, token);
  if (response.status === 401) redirect("/login");
  if (response.status === 404) return null;
  if (!response.ok) throw await errorOf(response);
  return (await response.json()) as T;
}

/** A write from a server action. Errors come back as values so the form can show them. */
export async function post<T>(
  path: string,
  body: unknown,
): Promise<{ ok: true; data: T } | { ok: false; error: ApiError }> {
  const token = await readToken();
  if (!token) redirect("/login");
  const response = await call(
    path,
    {
      method: "POST",
      body: JSON.stringify(body),
      headers: { "Content-Type": "application/json" },
    },
    token,
  );
  if (response.status === 401) redirect("/login");
  if (!response.ok) return { ok: false, error: await errorOf(response) };
  return { ok: true, data: (await response.json()) as T };
}

/** Check a token the user pasted, before it is stored. */
export async function verifyToken<T>(token: string): Promise<T | null> {
  const response = await call("/me", { method: "GET" }, token);
  return response.ok ? ((await response.json()) as T) : null;
}
