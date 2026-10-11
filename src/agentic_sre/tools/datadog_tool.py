"""Datadog integration: pulls metrics, logs, APM traces, and events for an
incident window, and provides a lightweight heuristic for verifying recovery.

All calls degrade gracefully (log a warning, return empty data) if DD_API_KEY /
DD_APP_KEY are not configured, so the rest of the agent can be exercised/tested
without live Datadog credentials.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from ..config import get_settings
from ..models import TelemetrySnapshot

logger = logging.getLogger("agentic_sre.tools.datadog")

_LOOKBACK_SECONDS = 15 * 60  # 15 minute window around the alert


def _configured() -> bool:
    settings = get_settings()
    return bool(settings.dd_api_key and settings.dd_app_key)


def _client_configuration():
    from datadog_api_client import Configuration

    settings = get_settings()
    config = Configuration()
    config.api_key["apiKeyAuth"] = settings.dd_api_key
    config.api_key["appKeyAuth"] = settings.dd_app_key
    config.server_variables["site"] = settings.dd_site
    return config


def query_metrics(query: str, from_ts: Optional[int] = None, to_ts: Optional[int] = None) -> dict:
    if not _configured():
        logger.warning("Datadog not configured; skipping metrics query")
        return {}
    from datadog_api_client import ApiClient
    from datadog_api_client.v1.api.metrics_api import MetricsApi

    to_ts = to_ts or int(time.time())
    from_ts = from_ts or to_ts - _LOOKBACK_SECONDS
    try:
        with ApiClient(_client_configuration()) as api_client:
            resp = MetricsApi(api_client).query_metrics(_from=from_ts, to=to_ts, query=query)
            return resp.to_dict()
    except Exception:
        logger.exception("metrics query failed for %s", query)
        return {}


def search_logs(query: str, from_ts: Optional[int] = None, to_ts: Optional[int] = None, limit: int = 50) -> list[dict]:
    if not _configured():
        logger.warning("Datadog not configured; skipping log search")
        return []
    from datadog_api_client import ApiClient
    from datadog_api_client.v2.api.logs_api import LogsApi
    from datadog_api_client.v2.model.logs_list_request import LogsListRequest
    from datadog_api_client.v2.model.logs_list_request_page import LogsListRequestPage
    from datadog_api_client.v2.model.logs_query_filter import LogsQueryFilter
    from datadog_api_client.v2.model.logs_sort import LogsSort

    to_ts = to_ts or int(time.time())
    from_ts = from_ts or to_ts - _LOOKBACK_SECONDS
    try:
        with ApiClient(_client_configuration()) as api_client:
            body = LogsListRequest(
                filter=LogsQueryFilter(
                    query=query,
                    _from=f"{from_ts * 1000}",
                    to=f"{to_ts * 1000}",
                ),
                sort=LogsSort.TIMESTAMP_DESCENDING,
                page=LogsListRequestPage(limit=limit),
            )
            resp = LogsApi(api_client).list_logs(body=body)
            return [item.to_dict() for item in (resp.data or [])]
    except Exception:
        logger.exception("log search failed for %s", query)
        return []


def search_traces(service: Optional[str], from_ts: Optional[int] = None, to_ts: Optional[int] = None, limit: int = 20) -> list[dict]:
    if not _configured() or not service:
        return []
    from datadog_api_client import ApiClient
    from datadog_api_client.v2.api.spans_api import SpansApi
    from datadog_api_client.v2.model.spans_list_request import SpansListRequest
    from datadog_api_client.v2.model.spans_list_request_data import SpansListRequestData
    from datadog_api_client.v2.model.spans_list_request_attributes import SpansListRequestAttributes
    from datadog_api_client.v2.model.spans_query_filter import SpansQueryFilter
    from datadog_api_client.v2.model.spans_query_options import SpansQueryOptions
    from datadog_api_client.v2.model.spans_list_request_type import SpansListRequestType

    to_ts = to_ts or int(time.time())
    from_ts = from_ts or to_ts - _LOOKBACK_SECONDS
    try:
        with ApiClient(_client_configuration()) as api_client:
            body = SpansListRequest(
                data=SpansListRequestData(
                    attributes=SpansListRequestAttributes(
                        filter=SpansQueryFilter(
                            query=f"service:{service} status:error",
                            _from=f"{from_ts * 1000}",
                            to=f"{to_ts * 1000}",
                        ),
                        options=SpansQueryOptions(timezone="UTC"),
                        page={"limit": limit},
                    ),
                    type=SpansListRequestType.SEARCH_REQUEST,
                )
            )
            resp = SpansApi(api_client).list_spans(body=body)
            return [item.to_dict() for item in (resp.data or [])]
    except Exception:
        logger.exception("trace search failed for service %s", service)
        return []


def search_events(query: str, from_ts: Optional[int] = None, to_ts: Optional[int] = None) -> list[dict]:
    if not _configured():
        return []
    from datadog_api_client import ApiClient
    from datadog_api_client.v1.api.events_api import EventsApi

    to_ts = to_ts or int(time.time())
    from_ts = from_ts or to_ts - _LOOKBACK_SECONDS
    try:
        with ApiClient(_client_configuration()) as api_client:
            resp = EventsApi(api_client).list_events(start=from_ts, end=to_ts, tags=query)
            return [event.to_dict() for event in (resp.events or [])]
    except Exception:
        logger.exception("event search failed for %s", query)
        return []


def gather_incident_telemetry(service: Optional[str], tags: list[str], alert_title: str) -> TelemetrySnapshot:
    """Orchestrates individual queries into a single snapshot used for both
    diagnosis and post-remediation verification."""
    tag_query = " ".join(tags) if tags else ""
    service_query = f"service:{service}" if service else tag_query

    metrics = query_metrics(f"avg:trace.http.request.errors{{{tag_query or 'env:production'}}}.as_count()")
    logs = search_logs(f"status:error {service_query}".strip())
    traces = search_traces(service)
    events = search_events(tag_query or alert_title)

    return TelemetrySnapshot(metrics=metrics, logs=logs, traces=traces, events=events)


def telemetry_indicates_recovery(fresh: TelemetrySnapshot, baseline: Optional[TelemetrySnapshot]) -> bool:
    """Heuristic recovery check: fewer error logs/traces than pre-remediation
    baseline, and no fresh crash-loop pod events."""
    if baseline is None:
        return len(fresh.logs) == 0 and len(fresh.traces) == 0

    fresh_error_signal = len(fresh.logs) + len(fresh.traces)
    baseline_error_signal = len(baseline.logs) + len(baseline.traces)
    crash_looping = any(
        "CrashLoopBackOff" in str(pod.get("status", "")) for pod in fresh.k8s_pod_status
    )
    return (fresh_error_signal < baseline_error_signal) and not crash_looping
