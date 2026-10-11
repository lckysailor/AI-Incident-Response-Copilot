"""Pydantic domain models shared across the agent, tools, and API layer."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

RiskLevel = Literal["low", "medium", "high"]
IncidentStatus = Literal[
    "new",
    "diagnosing",
    "planning",
    "awaiting_approval",
    "remediating",
    "verifying",
    "resolved",
    "escalated",
    "failed",
]


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Alert(BaseModel):
    """Normalized representation of an inbound Datadog monitor notification."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    monitor_id: Optional[str] = None
    title: str
    message: str = ""
    alert_type: str = "error"
    priority: str = "P3"
    tags: list[str] = Field(default_factory=list)
    source: str = "datadog"
    triggered_at: datetime = Field(default_factory=_now)
    raw_payload: dict[str, Any] = Field(default_factory=dict)

    def service_tag(self) -> Optional[str]:
        for tag in self.tags:
            if tag.startswith("service:"):
                return tag.split(":", 1)[1]
        return None

    def pod_or_deployment_tag(self, key: str) -> Optional[str]:
        for tag in self.tags:
            if tag.startswith(f"{key}:"):
                return tag.split(":", 1)[1]
        return None


class TelemetrySnapshot(BaseModel):
    """Metrics/logs/traces/events pulled from Datadog and Kubernetes for the incident window."""

    metrics: dict[str, Any] = Field(default_factory=dict)
    logs: list[dict[str, Any]] = Field(default_factory=list)
    traces: list[dict[str, Any]] = Field(default_factory=list)
    events: list[dict[str, Any]] = Field(default_factory=list)
    k8s_pod_status: list[dict[str, Any]] = Field(default_factory=list)
    k8s_recent_events: list[dict[str, Any]] = Field(default_factory=list)
    queried_at: datetime = Field(default_factory=_now)


class Diagnosis(BaseModel):
    """Root-cause hypothesis produced by the diagnostic LLM."""

    root_cause: str
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: list[str] = Field(default_factory=list)
    affected_resource: Optional[str] = None


class RemediationAction(BaseModel):
    """Specific operational action planned against Kubernetes."""

    action_type: Literal[
        "restart_deployment",
        "scale_deployment",
        "rollback_deployment",
        "no_action",
        "manual",
    ]
    target_namespace: str
    target_name: str
    params: dict[str, Any] = Field(default_factory=dict)
    risk_level: RiskLevel
    rationale: str


class RemediationPlan(BaseModel):
    """Structured plan composed of candidate actions and safety risk level."""

    actions: list[RemediationAction] = Field(default_factory=list)
    overall_risk: RiskLevel = "low"
    runbook_matched: Optional[str] = None


class Incident(BaseModel):
    """Top-level record tracked across the lifetime of an agent run."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    alert: Alert
    status: IncidentStatus = "new"
    telemetry: Optional[TelemetrySnapshot] = None
    diagnosis: Optional[Diagnosis] = None
    remediation_plan: Optional[RemediationPlan] = None
    actions_taken: list[dict[str, Any]] = Field(default_factory=list)
    retries: int = 0
    postmortem_markdown: Optional[str] = None
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)

    def touch(self) -> None:
        self.updated_at = _now()
