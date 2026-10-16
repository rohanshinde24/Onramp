"""Writes onramp-dashboard.json. Edit the panels here, then run:

    python3 deploy/observability/build_dashboard.py
"""

import json
from pathlib import Path

PROM = {"type": "prometheus", "uid": "prometheus"}
RATE = "[$__rate_interval]"


def panel(title, exprs, x, y, *, unit="short", kind="timeseries", w=8, h=8, legend=None):
    targets = [
        {"datasource": PROM, "expr": e, "legendFormat": (legend or {}).get(i, "__auto"), "refId": chr(65 + i)}
        for i, e in enumerate(exprs)
    ]
    return {
        "type": kind,
        "title": title,
        "datasource": PROM,
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "fieldConfig": {"defaults": {"unit": unit}, "overrides": []},
        "options": {"legend": {"displayMode": "list", "placement": "bottom"}},
        "targets": targets,
    }


def row(title, y):
    return {"type": "row", "title": title, "collapsed": False, "gridPos": {"x": 0, "y": y, "w": 24, "h": 1}}


def p95(metric, by):
    return f"histogram_quantile(0.95, sum by (le, {by}) (rate({metric}_bucket{RATE})))"


panels = [
    row("Cases and decisions", 0),
    panel("Case transitions by target state", [f"sum by (to) (rate(onramp_case_transitions_total{RATE}))"], 0, 1, unit="ops"),
    panel("Escalations by reason", [f'sum by (reason) (rate(onramp_case_transitions_total{{to="escalated"}}{RATE}))'], 8, 1, unit="ops"),
    panel("Approval decisions", [f"sum by (decision, risk_class) (rate(onramp_approvals_decisions_total{RATE}))"], 16, 1, unit="ops"),
    row("Action Gateway", 9),
    panel("Gateway outcomes", [f"sum by (outcome) (rate(onramp_gateway_executions_total{RATE}))"], 0, 10, unit="ops"),
    panel("Gateway p95 duration", [p95("onramp_gateway_duration_seconds", "outcome")], 8, 10, unit="s"),
    panel("Marketplace write attempts", [f"sum by (action_type, result) (rate(onramp_marketplace_write_attempts_total{RATE}))"], 16, 10, unit="ops"),
    row("Events", 18),
    panel("Outbox backlog", ["max by (source) (onramp_outbox_backlog)"], 0, 19),
    panel("Oldest unpublished event", ["max by (source) (onramp_outbox_oldest_age_seconds)"], 8, 19, unit="s"),
    panel("Consumer lag", ["max by (group) (onramp_consumer_lag)"], 16, 19, w=4),
    # Lag can only be read through the group coordinator; when it cannot, this keeps growing.
    panel("Lag reading age", ["max by (group) (onramp_consumer_lag_age_seconds)"], 20, 19, unit="s", w=4),
    panel("Consumed messages by outcome", [f"sum by (group, outcome) (rate(onramp_consumer_messages_total{RATE}))"], 0, 27, unit="ops"),
    panel("Consumer p95 processing time", [p95("onramp_consumer_duration_seconds", "group")], 8, 27, unit="s"),
    panel("Outbox sends and failures", [f"sum by (topic) (rate(onramp_outbox_published_total{RATE}))", f"sum by (topic) (rate(onramp_outbox_publish_failures_total{RATE}))"], 16, 27, unit="ops", legend={0: "sent {{topic}}", 1: "failed {{topic}}"}),
    row("Workflows (Temporal SDK)", 35),
    panel("Activity schedule-to-start p95", [p95("temporal_activity_schedule_to_start_latency_seconds", "task_queue")], 0, 36, unit="s"),
    panel("Activity failures", [f"sum by (activity_type) (rate(temporal_activity_execution_failed_total{RATE}))"], 8, 36, unit="ops"),
    panel("Workflow completions and failures", [f"sum (rate(temporal_workflow_completed_total{RATE}))", f"sum (rate(temporal_workflow_failed_total{RATE}))"], 16, 36, unit="ops", legend={0: "completed", 1: "failed"}),
    row("Checks and investigation", 44),
    panel("Check runs by outcome", [f"sum by (outcome) (rate(onramp_checks_runs_total{RATE}))"], 0, 45, unit="ops"),
    panel("Investigations by status", [f"sum by (status, model) (rate(onramp_investigation_runs_total{RATE}))"], 8, 45, unit="ops"),
    panel("Model tokens", [f"sum by (direction, model) (rate(onramp_investigation_tokens_total{RATE}))"], 16, 45, unit="short"),
    panel("Model call p95", [p95("onramp_model_call_duration_seconds", "model")], 0, 53, unit="s"),
    panel("Policy search p95", [p95("onramp_retrieval_duration_seconds", "retriever")], 8, 53, unit="s"),
    panel("Tool calls", [f"sum by (tool, result) (rate(onramp_agent_tool_calls_total{RATE}))"], 16, 53, unit="ops"),
    row("API", 61),
    panel("Requests by status", [f'sum by (http_status_code) (rate(http_server_duration_milliseconds_count{{service_name="onramp-api"}}{RATE}))'], 0, 62, unit="reqps", w=12),
    panel("Request p95 latency", [p95('http_server_duration_milliseconds', "http_target")], 12, 62, unit="ms", w=12),
]

dashboard = {
    "uid": "onramp",
    "title": "Onramp",
    "tags": ["onramp"],
    "timezone": "utc",
    "schemaVersion": 39,
    "refresh": "10s",
    "time": {"from": "now-1h", "to": "now"},
    "panels": panels,
}
out = Path(__file__).with_name("onramp-dashboard.json")
out.write_text(json.dumps(dashboard, indent=2) + "\n")
print(f"wrote {out} with {len(panels)} panels")
