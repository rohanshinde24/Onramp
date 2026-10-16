"use client";

// Catches failures anywhere under the root layout, including the console layout's own read of
// the signed-in user, so an API outage shows this instead of a blank page. Server error
// messages are withheld from the browser in production; the digest matches the server log.
export default function ErrorPage({
  error,
  retry,
}: {
  error: Error & { digest?: string };
  retry: () => void;
}) {
  return (
    <main className="mx-auto max-w-xl px-6 py-20">
      <h1 className="text-lg font-semibold">The console could not load this page</h1>
      <p className="mt-2 text-muted">
        The Onramp API may be unavailable or returned an error. Nothing was changed. Try again,
        and if it keeps failing, check the API&apos;s readiness at <code>/readyz</code>.
      </p>
      {error.digest && (
        <p className="mt-2 text-xs text-muted">
          Reference <code>{error.digest}</code>
        </p>
      )}
      <button
        onClick={() => retry()}
        className="mt-4 rounded bg-accent px-3 py-1.5 font-medium text-white dark:text-black"
      >
        Try again
      </button>
    </main>
  );
}
