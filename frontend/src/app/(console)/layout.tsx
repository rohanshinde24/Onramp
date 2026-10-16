import Link from "next/link";

import { signOut } from "@/app/login/actions";
import { getMe } from "@/lib/me";

export default async function ConsoleLayout({ children }: LayoutProps<"/">) {
  const me = await getMe();
  return (
    <>
      <header className="border-b border-border bg-surface">
        <div className="mx-auto flex max-w-7xl items-center gap-6 px-6 py-3">
          <Link href="/cases" className="font-semibold">
            Onramp
          </Link>
          <nav className="flex gap-4 text-muted">
            <Link href="/cases" className="hover:text-foreground">
              Cases
            </Link>
            <Link href="/evaluations" className="hover:text-foreground">
              Evaluations
            </Link>
          </nav>
          <div className="ml-auto flex items-center gap-3 text-muted">
            <span>
              {me.display_name} · <span className="capitalize">{me.role}</span> ·{" "}
              {me.tenant_name}
            </span>
            <form action={signOut}>
              <button className="hover:text-foreground">Sign out</button>
            </form>
          </div>
        </div>
      </header>
      <main className="mx-auto w-full max-w-7xl flex-1 px-6 py-6">{children}</main>
    </>
  );
}
