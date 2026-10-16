"use client";

import { type ReactNode, useActionState } from "react";

export type ActionState = { error: string | null };
type Action = (state: ActionState, form: FormData) => Promise<ActionState>;

/** A form bound to a server action that reports the API's refusal next to the control. */
export function ActionForm({
  action,
  submit,
  tone = "primary",
  children,
}: {
  action: Action;
  submit: string;
  tone?: "primary" | "secondary" | "danger";
  children?: ReactNode;
}) {
  const [state, formAction, pending] = useActionState(action, { error: null });
  const style = {
    primary: "bg-accent text-white dark:text-black",
    secondary: "border border-border bg-surface",
    danger: "bg-red-700 text-white",
  }[tone];
  return (
    <form action={formAction} className="space-y-2">
      {children}
      {state.error && (
        <p role="alert" className="text-red-700 dark:text-red-400">
          {state.error}
        </p>
      )}
      <button
        disabled={pending}
        className={`rounded px-3 py-1.5 font-medium disabled:opacity-60 ${style}`}
      >
        {pending ? "Working…" : submit}
      </button>
    </form>
  );
}
