"use client";

import { useActionState } from "react";

import { signIn } from "./actions";

export function SignInForm() {
  const [state, action, pending] = useActionState(signIn, { error: null });
  return (
    <form action={action} className="mt-6 space-y-3">
      <label className="block">
        <span className="text-muted">Bearer token</span>
        <textarea
          name="token"
          required
          rows={4}
          autoComplete="off"
          spellCheck={false}
          className="mono mt-1 block w-full rounded border border-border bg-surface p-2 text-xs"
        />
      </label>
      {state.error && (
        <p role="alert" className="text-red-700 dark:text-red-400">
          {state.error}
        </p>
      )}
      <button
        disabled={pending}
        className="rounded bg-accent px-3 py-1.5 font-medium text-white disabled:opacity-60 dark:text-black"
      >
        {pending ? "Checking…" : "Sign in"}
      </button>
    </form>
  );
}
