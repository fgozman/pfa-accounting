"""Generate ``dashboards/pfa-audit.json`` (run after editing; commit both).

    python observability/grafana/build_dashboard.py

Sources and why:

* **Prometheus** (OpenTelemetry metrics) for traffic over time: request rates, error
  ratio, latency percentiles, worker durations.  Exemplars link a latency point to
  a trace.
* **Loki** (structured logs) for exact totals and for audit and security: every
  event is one line, so rare events (one 401, one token replay) are counted exactly
  and can be read in full.
* **Tempo** for traces: slow requests and the service graph.

Labels and field names follow the logging contract in ``docs/logging-inventory.md``
(Loki extracts dotted JSON keys with underscores: ``pfa.audit.action`` ->
``pfa_audit_action``).
"""

from __future__ import annotations

import json
from pathlib import Path

LOKI = {"type": "loki", "uid": "pfa-loki"}
PROMETHEUS = {"type": "prometheus", "uid": "pfa-prometheus"}
TEMPO = {"type": "tempo", "uid": "pfa-tempo"}

LOG_SELECTOR = 'service_name=~"$service", env=~"$env"'
METRIC_SELECTOR = 'job=~"$service", deployment_environment_name=~"$env"'

panels: list[dict] = []


def _place(panel: dict, x: int, y: int, w: int, h: int) -> None:
    panel.update({"id": len(panels) + 1, "gridPos": {"x": x, "y": y, "w": w, "h": h}})
    panels.append(panel)


def row(title: str, y: int) -> None:
    _place({"type": "row", "title": title, "collapsed": False, "panels": []}, 0, y, 24, 1)


def loki_target(expr: str, *, instant: bool = False, legend: str | None = None) -> dict:
    target = {"refId": "A", "datasource": LOKI, "expr": expr, "editorMode": "code",
              "queryType": "instant" if instant else "range"}
    if legend:
        target["legendFormat"] = legend
    return target


def prom_target(expr: str, *, instant: bool = False, legend: str | None = None) -> dict:
    target = {"refId": "A", "datasource": PROMETHEUS, "expr": expr, "editorMode": "code",
              "instant": instant, "range": not instant, "exemplar": not instant}
    if legend:
        target["legendFormat"] = legend
    return target


def stat(title: str, target: dict, color: str, description: str, unit: str = "short") -> dict:
    return {
        "type": "stat", "title": title, "description": description, "datasource": target["datasource"],
        "targets": [target],
        "options": {"reduceOptions": {"calcs": ["lastNotNull"]}, "colorMode": "background", "graphMode": "none"},
        "fieldConfig": {"defaults": {"color": {"mode": "fixed", "fixedColor": color}, "unit": unit,
                                     "noValue": "0", "decimals": 0}, "overrides": []},
    }


def series(title: str, target: dict, unit: str = "short", description: str = "") -> dict:
    return {
        "type": "timeseries", "title": title, "description": description, "datasource": target["datasource"],
        "targets": [target],
        "fieldConfig": {"defaults": {"unit": unit, "custom": {"drawStyle": "line", "fillOpacity": 10}}, "overrides": []},
        "options": {"legend": {"displayMode": "table", "placement": "right", "calcs": ["mean", "max"]}},
    }


def logs(title: str, expr: str, description: str = "") -> dict:
    return {
        "type": "logs", "title": title, "description": description, "datasource": LOKI,
        "targets": [loki_target(expr)],
        "options": {"showTime": True, "wrapLogMessage": True, "sortOrder": "Descending", "enableLogDetails": True},
    }


# ----------------------------------------------------------------------------
row("Overview (selected time range)", 0)
# exact totals come from the logs (one line per event): Prometheus increase()
# extrapolates and misses a series' first sample, so a single 401 could read 0
_place(stat("Requests", loki_target(
    f'sum(count_over_time({{{LOG_SELECTOR}, event="http.request"}} [$__range]))', instant=True),
    "blue", "HTTP requests handled (health probes and CORS preflights excluded)."), 0, 1, 4, 4)
_place(stat("Server errors (5xx)", loki_target(
    f'sum(count_over_time({{{LOG_SELECTOR}, event="http.request", level="error"}} [$__range]))', instant=True),
    "red", "Requests answered with a 5xx status."), 4, 1, 4, 4)
_place(stat("Refused (401/403/429)", loki_target(
    f'sum(count_over_time({{{LOG_SELECTOR}, event="http.request", level="warn"}} [$__range]))', instant=True),
    "orange", "Unauthenticated, forbidden or rate-limited requests."), 8, 1, 4, 4)
_place(stat("Failed logins", loki_target(
    f'sum(count_over_time({{{LOG_SELECTOR}, event="audit"}} | json | pfa_audit_action="account.login_failed" [$__range]))',
    instant=True), "orange", "account.login_failed audit events (exact count from the logs)."), 12, 1, 4, 4)
_place(stat("Token/code replays", loki_target(
    f'sum(count_over_time({{{LOG_SELECTOR}, event=~"oauth.(code|refresh_token)_reuse_detected"}} [$__range]))',
    instant=True), "red", "Replayed authorization codes or refresh tokens: each one revoked a session family."), 16, 1, 4, 4)
_place(stat("Worker failures", loki_target(
    f'sum(count_over_time({{{LOG_SELECTOR}, event=~"job.failed|outbox.failed"}} [$__range]))',
    instant=True), "purple", "Failed background jobs and outbox deliveries (retried unless final)."), 20, 1, 4, 4)

# ----------------------------------------------------------------------------
row("Traffic (metrics)", 5)
_place(series("Request rate by status", prom_target(
    f"sum by (http_response_status_code) (rate(http_server_request_duration_seconds_count{{{METRIC_SELECTOR}}}[$__rate_interval]))",
    legend="{{http_response_status_code}}"), unit="reqps"), 0, 6, 12, 8)
_place(series("p95 latency by route", prom_target(
    f"histogram_quantile(0.95, sum by (le, http_route) (rate(http_server_request_duration_seconds_bucket{{{METRIC_SELECTOR}}}[$__rate_interval])))",
    legend="{{http_route}}"), unit="s",
    description="95th percentile per route template. Exemplar dots open the trace of a sampled request."), 12, 6, 12, 8)
_place(series("Server error ratio", prom_target(
    f'sum(rate(http_server_request_duration_seconds_count{{{METRIC_SELECTOR}, http_response_status_code=~"5.."}}[$__rate_interval]))'
    f" / sum(rate(http_server_request_duration_seconds_count{{{METRIC_SELECTOR}}}[$__rate_interval]))",
    legend="5xx ratio"), unit="percentunit"), 0, 14, 8, 7)
_place(series("Worker p95 duration by kind", prom_target(
    "histogram_quantile(0.95, sum by (le, pfa_work_kind) (rate(pfa_worker_work_duration_seconds_bucket[$__rate_interval])))",
    legend="{{pfa_work_kind}}"), unit="s"), 8, 14, 8, 7)
_place(series("Structured events by name", prom_target(
    f"sum by (event_name) (rate(pfa_events_total{{{METRIC_SELECTOR}}}[$__rate_interval]))",
    legend="{{event_name}}"), unit="ops", description="pfa.events: every structured log event, counted."), 16, 14, 8, 7)

# ----------------------------------------------------------------------------
row("Audit and security (logs)", 21)
_place(series("Audit events by action", loki_target(
    f'sum by (pfa_audit_action) (count_over_time({{{LOG_SELECTOR}, event="audit"}} | json | __error__="" [$__auto]))',
    legend="{{pfa_audit_action}}")), 0, 22, 12, 8)
_place(series("Security events", loki_target(
    f'sum by (event) (count_over_time({{{LOG_SELECTOR}, event=~"oauth\\\\..+"}} [$__auto]))',
    legend="{{event}}"), description="Login refusals, exhausted attempts, invalid proofs, origin mismatches, replays, revocations, refreshes."),
    12, 22, 12, 8)
_place(logs("Audit trail",
    f'{{{LOG_SELECTOR}, event="audit"}} | json | line_format "{{{{.pfa_audit_action}}}}  user={{{{.user_id}}}}  '
    f'entity={{{{.pfa_audit_entity_type}}}}:{{{{.pfa_audit_entity_id}}}}  workspace={{{{.pfa_workspace_id}}}}  '
    f'ip={{{{.client_address}}}}  correlation={{{{.pfa_correlation_id}}}}"',
    "Every committed audit_events row, as logged. Expand a line for all fields and the link to its trace."),
    0, 30, 24, 10)
_place(logs("Security events (detail)", f'{{{LOG_SELECTOR}, event=~"oauth\\\\..+"}} | json'), 0, 40, 12, 9)
_place(logs("Errors", f'{{{LOG_SELECTOR}, level=~"error|fatal"}} | json'), 12, 40, 12, 9)

# ----------------------------------------------------------------------------
row("Traces", 49)
_place({
    "type": "table", "title": "Slowest recent requests", "datasource": TEMPO,
    "description": "Traces slower than 100 ms. Click a trace id to open it (spans, SQL, logs).",
    "targets": [{"refId": "A", "datasource": TEMPO, "queryType": "traceql", "limit": 20, "tableType": "traces",
                 "query": '{resource.service.name=~"pfa-.+" && kind=server && duration > 100ms}'}],
}, 0, 50, 12, 10)
_place({
    "type": "nodeGraph", "title": "Service graph", "datasource": TEMPO,
    "targets": [{"refId": "A", "datasource": TEMPO, "queryType": "serviceMap"}],
}, 12, 50, 12, 10)
_place(logs("All lines of one correlation id",
    f'{{{LOG_SELECTOR}}} | json | pfa_correlation_id="$correlation_id"',
    "Enter the X-Correlation-Id of a response (or the correlation_id of an audit row) above."), 0, 60, 24, 10)

dashboard = {
    "uid": "pfa-audit",
    "title": "PFA — audit, security and traffic",
    "tags": ["pfa", "audit", "security", "opentelemetry"],
    "timezone": "browser",
    "schemaVersion": 41,
    "version": 2,
    "editable": False,
    "time": {"from": "now-6h", "to": "now"},
    "refresh": "30s",
    "templating": {"list": [
        {"name": "service", "label": "Service", "type": "query", "datasource": LOKI,
         "query": {"label": "service_name", "refId": "LokiVariableQueryEditor-VariableQuery", "stream": "", "type": 1},
         "definition": "label_values(service_name)", "includeAll": True, "multi": True, "allValue": ".+",
         "current": {"text": "All", "value": "$__all"}, "refresh": 2, "sort": 1},
        {"name": "env", "label": "Environment", "type": "query", "datasource": LOKI,
         "query": {"label": "env", "refId": "LokiVariableQueryEditor-VariableQuery", "stream": "", "type": 1},
         "definition": "label_values(env)", "includeAll": True, "multi": True, "allValue": ".+",
         "current": {"text": "All", "value": "$__all"}, "refresh": 2, "sort": 1},
        {"name": "correlation_id", "label": "Correlation id", "type": "textbox", "query": "",
         "current": {"text": "", "value": ""}},
    ]},
    "panels": panels,
    "annotations": {"list": []},
}

target = Path(__file__).resolve().parent / "dashboards" / "pfa-audit.json"
target.write_text(json.dumps(dashboard, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
print(f"wrote {target} ({len(panels)} panels)")
