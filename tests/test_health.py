from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO

from wsgiref.util import setup_testing_defaults

from graphiti.config import GraphitiConfig
from graphiti.health import collect_health_metrics, format_dashboard, HealthApp
from graphiti.state import GraphitiStateStore


def test_collect_health_metrics_handles_missing_state(tmp_path):
    store = GraphitiStateStore(base_dir=tmp_path / "state")
    config = GraphitiConfig(
        group_id="g",
        poll_gmail_drive_calendar_seconds=3600,
        poll_slack_active_seconds=30,
    )
    metrics = collect_health_metrics(store, config, now=datetime(2024, 1, 1, tzinfo=timezone.utc))
    assert metrics["status"] == "pending"
    assert metrics["sources"]["gmail"]["status"] == "pending"


def test_collect_health_metrics_detects_stale(tmp_path):
    store = GraphitiStateStore(base_dir=tmp_path / "state")
    store.update_state(
        {
            "gmail": {
                "last_run_at": "2024-01-01T00:00:00Z",
                "error_count": 0,
            }
        }
    )
    config = GraphitiConfig(
        group_id="g",
        poll_gmail_drive_calendar_seconds=60,
        poll_slack_active_seconds=30,
    )
    metrics = collect_health_metrics(
        store,
        config,
        now=datetime(2024, 1, 1, 0, 5, tzinfo=timezone.utc),
    )
    assert metrics["sources"]["gmail"]["status"] == "stale"
    assert metrics["status"] == "stale"


def test_format_dashboard_outputs_table(tmp_path):
    store = GraphitiStateStore(base_dir=tmp_path / "state")
    store.update_state({"slack": {"last_run_at": "2024-01-01T00:00:00Z"}})
    config = GraphitiConfig(group_id="g")
    metrics = collect_health_metrics(store, config)
    output = format_dashboard(metrics)
    assert "Personal Assistant Sync Status" in output
    assert "slack" in output


def test_health_app_returns_json(tmp_path):
    store = GraphitiStateStore(base_dir=tmp_path / "state")
    store.update_state({"gmail": {"last_run_at": "2024-01-01T00:00:00Z"}})
    app = HealthApp(config=GraphitiConfig(group_id="g"), state_store=store)

    environ: dict[str, object] = {}
    setup_testing_defaults(environ)
    environ["PATH_INFO"] = "/health"
    environ["REQUEST_METHOD"] = "GET"
    body = BytesIO()

    def start_response(status, headers):
        body.status = status
        body.headers = headers

    result = app(environ, start_response)
    payload = b"".join(result)
    assert body.status == "200 OK"
    assert payload.startswith(b"{")
