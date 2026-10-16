import "server-only";

import { cookies } from "next/headers";

// The bearer token lives only in an httpOnly cookie read by the console's server. Browser
// code never sees it and never calls the API directly.
const COOKIE = "onramp_token";

export async function readToken(): Promise<string | undefined> {
  return (await cookies()).get(COOKIE)?.value;
}

export async function storeToken(token: string): Promise<void> {
  (await cookies()).set(COOKIE, token, {
    httpOnly: true,
    sameSite: "strict",
    secure: process.env.NODE_ENV === "production",
    path: "/",
    // Matches the API's token lifetime closely enough; an expired token reads as signed out.
    maxAge: 60 * 60 * 8,
  });
}

export async function clearToken(): Promise<void> {
  (await cookies()).delete(COOKIE);
}
