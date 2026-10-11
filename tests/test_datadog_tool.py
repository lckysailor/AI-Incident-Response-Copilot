from agentic_sre.tools import datadog_tool


def test_gather_telemetry_degrades_gracefully_without_credentials(monkeypatch):
    monkeypatch.setenv("DD_API_KEY", "")
    monkeypatch.setenv("DD_APP_KEY", "")

    snapshot = datadog_tool.gather_incident_telemetry(
        service="checkout", tags=["env:prod", "service:checkout"], alert_title="High error rate"
    )

    assert snapshot.metrics == {}
    assert snapshot.logs == []
    assert snapshot.traces == []
    assert snapshot.events == []


def test_telemetry_indicates_recovery_with_no_baseline_and_clean_signal():
    from agentic_sre.models import TelemetrySnapshot

    fresh = TelemetrySnapshot(logs=[], traces=[])
    assert datadog_tool.telemetry_indicates_recovery(fresh, baseline=None) is True


def test_telemetry_indicates_recovery_false_when_still_crashlooping():
    from agentic_sre.models import TelemetrySnapshot

    baseline = TelemetrySnapshot(logs=[{"a": 1}] * 5, traces=[{"a": 1}] * 5)
    fresh = TelemetrySnapshot(
        logs=[{"a": 1}], traces=[], k8s_pod_status=[{"status": "CrashLoopBackOff"}]
    )
    assert datadog_tool.telemetry_indicates_recovery(fresh, baseline=baseline) is False


def test_telemetry_indicates_recovery_true_when_error_signal_drops():
    from agentic_sre.models import TelemetrySnapshot

    baseline = TelemetrySnapshot(logs=[{"a": 1}] * 5, traces=[{"a": 1}] * 5)
    fresh = TelemetrySnapshot(logs=[], traces=[], k8s_pod_status=[{"status": "Running"}])
    assert datadog_tool.telemetry_indicates_recovery(fresh, baseline=baseline) is True
