"""LangGraph state definition for an incident-response run."""
from __future__ import annotations

from typing import Any, Optional, TypedDict

from ..models import Diagnosis, Incident, RemediationPlan, TelemetrySnapshot


class IncidentState(TypedDict, total=False):
    """Mutable state threaded through every node of the graph.

    LangGraph passes a dict-like state between nodes; each node returns a
    partial update that gets merged into this structure.
    """

    incident: Incident
    telemetry: Optional[TelemetrySnapshot]
    diagnosis: Optional[Diagnosis]
    remediation_plan: Optional[RemediationPlan]
    requires_approval: bool
    approved: Optional[bool]
    verification_passed: Optional[bool]
    retries: int
    max_retries: int
    log: list[str]
    error: Optional[str]
    scratch: dict[str, Any]
