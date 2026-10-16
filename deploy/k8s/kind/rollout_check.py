"""Sends steady API traffic through the kind NodePort while a Deployment rolls, then reports
every request that failed. `make k8s-rollout-check` runs it against onramp-api.

usage: rollout_check.py <deployment> [--url URL] [--workers N]
Reads the bearer token from ONRAMP_TOKEN. Uses only the standard library.
"""

import argparse
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import Counter


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("deployment")
    parser.add_argument("--url", default="http://127.0.0.1:8180/cases?limit=5")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--context", default="kind-onramp")
    args = parser.parse_args()
    token = os.environ.get("ONRAMP_TOKEN")
    if not token:
        sys.exit("ONRAMP_TOKEN is not set")

    stop = threading.Event()
    outcomes: Counter[str] = Counter()
    latencies: list[float] = []
    lock = threading.Lock()

    def send() -> None:
        request = urllib.request.Request(args.url, headers={"Authorization": f"Bearer {token}"})
        while not stop.is_set():
            started = time.monotonic()
            try:
                with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
                    outcome = str(response.status)
            except urllib.error.HTTPError as exc:
                outcome = str(exc.code)
            except OSError as exc:
                outcome = type(exc).__name__
            with lock:
                outcomes[outcome] += 1
                latencies.append(time.monotonic() - started)

    threads = [threading.Thread(target=send, daemon=True) for _ in range(args.workers)]
    for thread in threads:
        thread.start()
    time.sleep(3)  # traffic is flowing before the rollout starts
    kubectl = ["kubectl", "--context", args.context, "-n", "onramp"]
    started = time.monotonic()
    subprocess.run([*kubectl, "rollout", "restart", f"deployment/{args.deployment}"], check=True)  # noqa: S603
    subprocess.run(  # noqa: S603
        [*kubectl, "rollout", "status", f"deployment/{args.deployment}", "--timeout=300s"],
        check=True,
    )
    rollout_s = time.monotonic() - started
    time.sleep(3)  # and keeps flowing after it
    stop.set()
    for thread in threads:
        thread.join(timeout=15)

    total = sum(outcomes.values())
    failed = total - outcomes["200"]
    latencies.sort()
    p99 = latencies[int(len(latencies) * 0.99) - 1] if latencies else 0.0
    print(f"rollout of {args.deployment} took {rollout_s:.1f}s")
    print(f"requests={total} failed={failed} outcomes={dict(outcomes)} p99={p99 * 1000:.0f}ms")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
