"use server";

import { redirect } from "next/navigation";

import { verifyToken } from "@/lib/api";
import { clearToken, storeToken } from "@/lib/session";
import type { Me } from "@/lib/types";

export async function signIn(
  _: { error: string | null },
  form: FormData,
): Promise<{ error: string | null }> {
  const token = String(form.get("token") ?? "").trim();
  if (!token) return { error: "Paste a bearer token." };
  const me = await verifyToken<Me>(token).catch(() => null);
  if (!me) return { error: "The API did not accept that token." };
  if (me.role === "marketplace_service") {
    return { error: "Service identities create cases through the API, not the console." };
  }
  await storeToken(token);
  redirect("/cases");
}

export async function signOut(): Promise<void> {
  await clearToken();
  redirect("/login");
}
