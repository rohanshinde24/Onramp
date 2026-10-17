"""One load benchmark run of the intake pipeline: ``onramp-load-run --rate 20 --duration 60``.

Seeds fresh subjects, runs ``deploy/load/intake.js`` with k6 against the API, waits for the
pipeline to settle, then writes ``artifacts/load/<run_id>/``: the k6 summary, the pipeline
report read from PostgreSQL, and a manifest with the commit, the settings and, when a kind
cluster is reachable, the replica count of every Deployment. Local only.
"""

import argparse
import json
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import onramp.models  # noqa: F401 - every table must be mapped before any session is used
from onramp.benchmarks.load import (
    LOAD_TENANT,
    SERVICE_USER,
    ensure_tenant,
    pipeline_report,
    seed_subjects,
)
from onramp.config import AppEnv, get_settings
from onramp.db.engine import make_engine, make_session_factory
from onramp.evaluation.report import git_state
from onramp.identity.tokens import issue_token

ROOT = Path(__file__).resolve().parents[4]
SCRIPT = ROOT / "deploy" / "load" / "intake.js"
SETTLED_STATES = {"proposed", "resolved", "escalated", "closed"}


def main() -> None:
    parser = argparse.ArgumentParser(prog="onramp-load-run")
    parser.add_argument("--rate", type=int, required=True, help="intakes per second")
    parser.add_argument("--duration", type=int, required=True, help="seconds of load")
    parser.add_argument("--api", default="http://127.0.0.1:8180")
    parser.add_argument("--settle-timeout", type=int, default=600, help="seconds")
    parser.add_argument("--label", default="", help="free text kept in the manifest")
    parser.add_argument("--kube-context", default="kind-onramp")
    args = parser.parse_args()

    settings = get_settings()
    if settings.app_env is not AppEnv.LOCAL:
        sys.exit("onramp-load-run only runs with APP_ENV=local")
    k6 = shutil.which("k6")
    if k6 is None:
        sys.exit("k6 is not installed")

    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out = ROOT / "artifacts" / "load" / run_id
    out.mkdir(parents=True)
    onramp = make_session_factory(make_engine(str(settings.database_url)))
    marketplace = make_session_factory(make_engine(str(settings.marketplace_database_url)))

    count = args.rate * args.duration
    ensure_tenant(onramp)
    # k6 may run a few more iterations than rate times duration; seed a margin.
    seed_subjects(marketplace, run_id, count + args.rate * 5, datetime.now(UTC))
    token = issue_token(
        secret=settings.auth_jwt_secret.get_secret_value(),
        issuer=settings.auth_issuer,
        audience=settings.auth_audience,
        user_id=SERVICE_USER,
        tenant_id=LOAD_TENANT,
        now=datetime.now(UTC),
    )
    manifest: dict[str, Any] = {
        "run_id": run_id,
        "commit": git_state(ROOT)[0],
        "dirty": git_state(ROOT)[1],
        "rate_per_s": args.rate,
        "duration_s": args.duration,
        "api": args.api,
        "label": args.label,
        "deployments": _replicas(args.kube_context),
        "started_at": datetime.now(UTC).isoformat(),
    }
    print(f"run {run_id}: {args.rate}/s for {args.duration}s against {args.api}", flush=True)
    subprocess.run(  # noqa: S603 - resolved k6 binary, fixed script
        [
            k6,
            "run",
            "--quiet",
            "--summary-export",
            str(out / "k6-summary.json"),
            "-e",
            f"RUN_ID={run_id}",
            "-e",
            f"RATE={args.rate}",
            "-e",
            f"DURATION={args.duration}",
            "-e",
            f"API={args.api}",
            "-e",
            f"TOKEN={token}",
            str(SCRIPT),
        ],
        check=False,
    )
    manifest["load_finished_at"] = datetime.now(UTC).isoformat()

    report = _wait_until_settled(onramp, run_id, args.settle_timeout)
    manifest["settled_at"] = datetime.now(UTC).isoformat()
    (out / "pipeline.json").write_text(json.dumps(report, indent=2) + "\n")
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps({"k6": _k6_digest(out / "k6-summary.json"), "pipeline": report}, indent=2))
    print(f"artifacts: {out}")


def _wait_until_settled(onramp: Any, run_id: str, timeout_s: int) -> dict[str, Any]:
    """Poll until every case of the run has left the automated states, or progress stalls for
    ``timeout_s``."""
    last_progress = time.monotonic()
    settled_before = -1
    while True:
        report = pipeline_report(onramp, run_id)
        settled = sum(n for state, n in report["states"].items() if state in SETTLED_STATES)
        if report["cases"] and settled == report["cases"]:
            return report
        if settled != settled_before:
            settled_before, last_progress = settled, time.monotonic()
        elif time.monotonic() - last_progress > timeout_s:
            report["settle_timed_out"] = True
            return report
        time.sleep(2)


def _replicas(context: str) -> dict[str, int] | None:
    kubectl = shutil.which("kubectl")
    if kubectl is None:
        return None
    result = subprocess.run(  # noqa: S603 - resolved kubectl, fixed arguments
        [kubectl, "--context", context, "-n", "onramp", "get", "deployments", "-o", "json"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    return {
        item["metadata"]["name"]: item["spec"].get("replicas", 0)
        for item in json.loads(result.stdout)["items"]
    }


def _k6_digest(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    metrics = json.loads(path.read_text())["metrics"]
    duration = metrics.get("http_req_duration", {})
    failed = metrics.get("http_req_failed", {})
    return {
        "requests": metrics.get("http_reqs", {}).get("count"),
        "failed_rate": failed.get("value"),
        "duration_ms": {k: duration.get(k) for k in ("med", "p(95)", "p(99)", "max")},
        "dropped_iterations": metrics.get("dropped_iterations", {}).get("count", 0),
    }


if __name__ == "__main__":
    main()
