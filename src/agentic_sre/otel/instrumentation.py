"""OpenTelemetry instrumentation for the agent itself.

The agent's reasoning steps (LangGraph nodes) are wrapped in spans and shipped
via OTLP -- by default to an OpenTelemetry Collector which forwards to Datadog
APM/Logs/Metrics using Datadog's native OTLP ingestion. This means the incident-
response agent shows up in the same Datadog org it is remediating incidents for,
which is useful both operationally and for auditing what the agent did and why.
"""
from __future__ import annotations

import functools
import logging
from typing import Any, Callable, TypeVar

from ..config import get_settings

logger = logging.getLogger("agentic_sre.otel")

F = TypeVar("F", bound=Callable[..., Any])

_initialized = False
_tracer = None


def setup_telemetry() -> None:
    """Configures global TracerProvider/MeterProvider with OTLP exporters.

    Idempotent: safe to call multiple times. Only the first call takes effect.
    """
    global _initialized, _tracer
    if _initialized:
        return

    settings = get_settings()

    try:
        from opentelemetry import metrics, trace
        from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        resource = Resource.create(
            {
                "service.name": settings.otel_service_name,
                "deployment.environment": settings.otel_environment,
                "env": settings.otel_environment,
                "service": settings.otel_service_name,
            }
        )

        trace_provider = TracerProvider(resource=resource)
        trace_provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=settings.otel_exporter_otlp_endpoint, insecure=True))
        )
        trace.set_tracer_provider(trace_provider)

        metric_reader = PeriodicExportingMetricReader(
            OTLPMetricExporter(endpoint=settings.otel_exporter_otlp_endpoint, insecure=True)
        )
        metrics.set_meter_provider(MeterProvider(resource=resource, metric_readers=[metric_reader]))

        _tracer = trace.get_tracer(settings.otel_service_name)
        _initialized = True
        logger.info("OpenTelemetry configured: endpoint=%s", settings.otel_exporter_otlp_endpoint)
    except Exception:
        logger.warning("OpenTelemetry setup failed or skipped; tracing disabled")
        _initialized = True


def instrument_fastapi(app) -> None:
    """Attaches request-level tracing to the FastAPI app."""
    setup_telemetry()
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app)
    except Exception:
        logger.warning("Failed to instrument FastAPI app with OpenTelemetry")


def traced_node(node_name: str) -> Callable[[F], F]:
    """Decorator for LangGraph node functions: wraps execution in a span named
    `agent.node.<node_name>` and records the incident id + key state fields as
    span attributes."""

    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(state, *args, **kwargs):
            setup_telemetry()
            try:
                from opentelemetry import trace

                tracer = trace.get_tracer(get_settings().otel_service_name)
                incident = state.get("incident") if isinstance(state, dict) else None
                with tracer.start_as_current_span(f"agent.node.{node_name}") as span:
                    if incident is not None:
                        span.set_attribute("incident.id", incident.id)
                        span.set_attribute("incident.status", incident.status)
                        span.set_attribute("alert.title", incident.alert.title)
                    try:
                        result = func(state, *args, **kwargs)
                        span.set_attribute("node.success", True)
                        return result
                    except Exception as exc:
                        span.set_attribute("node.success", False)
                        span.record_exception(exc)
                        raise
            except ImportError:
                # If opentelemetry is not installed, run node directly
                return func(state, *args, **kwargs)

        return wrapper  # type: ignore[return-value]

    return decorator
