import Link from "next/link";

export default function NotFound() {
  return (
    <main className="mx-auto max-w-xl px-6 py-20">
      <h1 className="text-lg font-semibold">Not found</h1>
      <p className="mt-2 text-muted">
        Nothing here, or nothing your tenant can see. The two look the same on purpose.
      </p>
      <Link href="/cases" className="mt-4 inline-block text-accent hover:underline">
        Back to cases
      </Link>
    </main>
  );
}
