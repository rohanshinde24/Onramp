"""Print one trace from Tempo as an indented tree, skipping per-statement SQL spans.

usage: python3 deploy/observability/trace_tree.py <trace_id>
"""

import json
import subprocess
import sys

SQL = ("SELECT", "INSERT", "UPDATE", "DELETE", "BEGIN", "COMMIT", "ROLLBACK", "connect", "WITH")


def main(trace_id: str) -> None:
    raw = subprocess.run(
        ["docker", "compose", "exec", "-T", "observability", "sh", "-c",
         f"curl -s localhost:3200/api/traces/{trace_id}"],
        capture_output=True, text=True, check=True,
    ).stdout
    spans = {}
    for batch in json.loads(raw).get("batches", []):
        service = next(
            a["value"]["stringValue"]
            for a in batch["resource"]["attributes"]
            if a["key"] == "service.name"
        )
        for scope in batch.get("scopeSpans", []):
            for span in scope["spans"]:
                spans[span["spanId"]] = dict(span, service=service)
    children: dict[str | None, list[dict]] = {}
    for span in spans.values():
        parent = span.get("parentSpanId")
        children.setdefault(parent if parent in spans else None, []).append(span)

    def show(parent: str | None, depth: int) -> None:
        for span in sorted(children.get(parent, []), key=lambda s: int(s["startTimeUnixNano"])):
            if span["name"].startswith(SQL):
                continue
            ms = (int(span["endTimeUnixNano"]) - int(span["startTimeUnixNano"])) / 1e6
            status = span.get("status", {})
            error = status.get("code") in (2, "STATUS_CODE_ERROR")
            note = f"  ERROR: {status.get('message', '')}" if error else ""
            print(f"{'  ' * depth}{span['name']}  [{span['service']}] {ms:.1f}ms{note}")
            show(span["spanId"], depth + 1)

    show(None, 0)


if __name__ == "__main__":
    main(sys.argv[1])
