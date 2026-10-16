import type { Metadata } from "next";
import Link from "next/link";

import { Empty, StateBadge } from "@/components/ui";
import { get } from "@/lib/api";
import { TYPE_LABEL, age, humanize } from "@/lib/format";
import { QUEUES, type QueueId, queueOf } from "@/lib/queues";
import type { CasePage, CaseType } from "@/lib/types";

export const metadata: Metadata = { title: "Cases" };

const TYPES = Object.keys(TYPE_LABEL) as CaseType[];
const COUNTED: QueueId[] = ["decide", "approve", "escalated"];
const PAGE = 50;

function casesPath(queue: QueueId, type: CaseType | null, before?: string, limit = PAGE) {
  const params = new URLSearchParams();
  for (const state of QUEUES[queue].states) params.append("state", state);
  if (type) params.set("case_type", type);
  if (before) params.set("before", before);
  params.set("limit", String(limit));
  return `/cases?${params}`;
}

function href(queue: QueueId, type: CaseType | null, before?: string) {
  const params = new URLSearchParams({ queue });
  if (type) params.set("type", type);
  if (before) params.set("before", before);
  return `/cases?${params}`;
}

export default async function InboxPage({ searchParams }: PageProps<"/cases">) {
  const query = await searchParams;
  const queue = queueOf(query.queue);
  const rawType = typeof query.type === "string" ? query.type : null;
  const type = rawType && (TYPES as string[]).includes(rawType) ? (rawType as CaseType) : null;
  const before = typeof query.before === "string" ? query.before : undefined;

  const [page, ...counts] = await Promise.all([
    get<CasePage>(casesPath(queue, type, before)),
    ...COUNTED.map((q) => get<CasePage>(casesPath(q, type, undefined, 100))),
  ]);
  const countOf = new Map(COUNTED.map((q, i) => [q, counts[i].cases.length]));
  const now = new Date();

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        {(Object.keys(QUEUES) as QueueId[]).map((q) => {
          const count = countOf.get(q);
          return (
            <Link
              key={q}
              href={href(q, type)}
              aria-current={q === queue ? "page" : undefined}
              className={`rounded px-3 py-1.5 ${
                q === queue ? "bg-accent text-white dark:text-black" : "border border-border"
              }`}
            >
              {QUEUES[q].label}
              {count !== undefined && (
                <span className="ml-1.5 opacity-80">{count >= 100 ? "100+" : count}</span>
              )}
            </Link>
          );
        })}
        <div className="ml-auto flex gap-2 text-muted">
          <Link href={href(queue, null)} className={type === null ? "text-foreground" : ""}>
            All types
          </Link>
          {TYPES.map((t) => (
            <Link key={t} href={href(queue, t)} className={type === t ? "text-foreground" : ""}>
              {TYPE_LABEL[t]}
            </Link>
          ))}
        </div>
      </div>

      {page.cases.length === 0 ? (
        <Empty>No cases in this queue.</Empty>
      ) : (
        <table className="w-full border-collapse overflow-hidden rounded-md border border-border bg-surface">
          <thead className="text-left text-muted">
            <tr className="border-b border-border">
              <th className="px-3 py-2 font-medium">Case</th>
              <th className="px-3 py-2 font-medium">Type</th>
              <th className="px-3 py-2 font-medium">State</th>
              <th className="px-3 py-2 font-medium">Subject</th>
              <th className="px-3 py-2 font-medium">Worker</th>
              <th className="px-3 py-2 font-medium">Detail</th>
              <th className="px-3 py-2 text-right font-medium">Age</th>
            </tr>
          </thead>
          <tbody>
            {page.cases.map((c) => (
              <tr key={c.id} className="border-b border-border last:border-0 hover:bg-background">
                <td className="px-3 py-2">
                  <Link href={`/cases/${c.id}`} className="mono text-xs text-accent hover:underline">
                    {c.id}
                  </Link>
                </td>
                <td className="px-3 py-2">{TYPE_LABEL[c.case_type]}</td>
                <td className="px-3 py-2">
                  <StateBadge state={c.state} />
                </td>
                <td className="mono px-3 py-2 text-xs">{c.subject}</td>
                <td className="mono px-3 py-2 text-xs">{c.worker_id}</td>
                <td className="px-3 py-2 text-muted">
                  {c.escalation_reason
                    ? humanize(c.escalation_reason)
                    : c.resolution_code
                      ? c.resolution_code
                      : "n/a"}
                </td>
                <td className="px-3 py-2 text-right tabular-nums">{age(c.created_at, now)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {page.next_before && (
        <Link href={href(queue, type, page.next_before)} className="text-accent hover:underline">
          Older cases
        </Link>
      )}
    </div>
  );
}
