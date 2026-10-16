import type { Metadata } from "next";

import { SignInForm } from "./form";

export const metadata: Metadata = { title: "Sign in" };

export default function LoginPage() {
  return (
    <main className="mx-auto w-full max-w-md px-6 py-20">
      <h1 className="text-xl font-semibold">Onramp</h1>
      <p className="mt-2 text-muted">
        Sign in with a token for a user in your tenant. Locally, mint one with{" "}
        <code className="text-xs">backend/.venv/bin/onramp-dev-token acme acme_operator</code>.
      </p>
      <SignInForm />
    </main>
  );
}
