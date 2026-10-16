import type { ReactNode } from "react";

import { STATE_LABEL } from "@/lib/format";
import type { CaseState } from "@/lib/types";

export function Section({
  title,
  id,
  aside,
  children,
}: {
  title: string;
  id?: string;
  aside?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section id={id} className="rounded-md border border-border bg-surface">
      <header className="flex items-baseline justify-between border-b border-border px-4 py-2">
        <h2 className="font-semibold">{title}</h2>
        {aside && <div className="text-xs text-muted">{aside}</div>}
      </header>
      <div className="px-4 py-3">{children}</div>
    </section>
  );
}

const TONE: Record<string, string> = {
  neutral: "bg-neutral-100 text-neutral-700 dark:bg-neutral-800 dark:text-neutral-300",
  attention: "bg-amber-100 text-amber-900 dark:bg-amber-900/40 dark:text-amber-200",
  danger: "bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-200",
  good: "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-200",
  info: "bg-sky-100 text-sky-800 dark:bg-sky-900/40 dark:text-sky-200",
};

export type Tone = keyof typeof TONE;

export function Badge({ tone = "neutral", children }: { tone?: Tone; children: ReactNode }) {
  return (
    <span className={`inline-block rounded px-1.5 py-0.5 text-xs font-medium ${TONE[tone]}`}>
      {children}
    </span>
  );
}

const STATE_TONE: Partial<Record<CaseState, Tone>> = {
  proposed: "attention",
  awaiting_approval: "attention",
  escalated: "danger",
  resolved: "good",
  executing: "info",
  verifying: "info",
};

export function StateBadge({ state }: { state: CaseState }) {
  return <Badge tone={STATE_TONE[state] ?? "neutral"}>{STATE_LABEL[state]}</Badge>;
}

export function Fields({ rows }: { rows: [string, ReactNode][] }) {
  return (
    <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1">
      {rows.map(([label, value]) => (
        <div key={label} className="contents">
          <dt className="text-muted">{label}</dt>
          <dd className="min-w-0 break-words">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="text-muted">{children}</p>;
}

/** Text a worker, reporter or reviewer wrote. Shown as data, never as instructions. */
export function Untrusted({ label, text }: { label: string; text: string }) {
  return (
    <figure className="rounded border border-dashed border-border p-2">
      <figcaption className="text-xs text-muted">{label} (untrusted input)</figcaption>
      <blockquote className="mt-1 whitespace-pre-wrap">{text || "(empty)"}</blockquote>
    </figure>
  );
}

/** One anchor per record or passage, whether it is cited as "policy_passage:x" or as "x". */
export function anchorFor(ref: string): string {
  const id = ref.startsWith("policy_passage:") ? ref.slice("policy_passage:".length) : ref;
  return `ref-${id.replace(/[^A-Za-z0-9_-]/g, "-")}`;
}

/** An evidence reference or policy citation that jumps to the record or passage on the page. */
export function RefLink({ refId }: { refId: string }) {
  return (
    <a
      href={`#${anchorFor(refId)}`}
      className="mono rounded bg-neutral-100 px-1 text-xs text-accent hover:underline dark:bg-neutral-800"
    >
      {refId}
    </a>
  );
}

export function RefList({ refs }: { refs: string[] }) {
  if (refs.length === 0) return <span className="text-muted">none</span>;
  return (
    <span className="flex flex-wrap gap-1">
      {refs.map((r) => (
        <RefLink key={r} refId={r} />
      ))}
    </span>
  );
}
