"""FastAPI entrypoint: receives Datadog alert webhooks, exposes incident
status/approval endpoints, and wires up OpenTelemetry instrumentation.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from pydantic import BaseModel

from .agent.graph import get_incident_state, resume_incident, run_incident
from .config import get_settings
from .models import Alert, Incident
from .otel.instrumentation import instrument_fastapi

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("agentic_sre.main")

app = FastAPI(title="Agentic SRE", version="0.1.0")
instrument_fastapi(app)


class DatadogWebhookPayload(BaseModel):
    """Loosely typed Datadog monitor webhook body.

    Configure the Datadog webhook payload as:
    {"id": "$ID", "title": "$TITLE", "body": "$TEXT_ONLY_MSG",
     "alert_type": "$ALERT_TYPE", "priority": "$PRIORITY", "tags": "$TAGS"}
    """

    id: Optional[str] = None
    title: str = "Untitled alert"
    body: str = ""
    alert_type: str = "error"
    priority: str = "P3"
    tags: Any = ""  # Datadog sends a comma-separated string by default

    def to_alert(self) -> Alert:
        tags = self.tags
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",") if t.strip()]
        return Alert(
            monitor_id=self.id,
            title=self.title,
            message=self.body,
            alert_type=self.alert_type,
            priority=self.priority,
            tags=tags or [],
            raw_payload=self.model_dump(),
        )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/webhooks/datadog", status_code=202)
def datadog_webhook(
    payload: DatadogWebhookPayload,
    background_tasks: BackgroundTasks,
    secret: str = Query(default=""),
) -> dict[str, str]:
    settings = get_settings()
    if settings.agent_webhook_secret and secret != settings.agent_webhook_secret:
        raise HTTPException(status_code=401, detail="invalid webhook secret")

    alert = payload.to_alert()
    incident = Incident(alert=alert)
    logger.info("received alert, starting incident %s: %s", incident.id, alert.title)

    background_tasks.add_task(run_incident, incident)
    return {"incident_id": incident.id, "status": "accepted"}


@app.get("/incidents/{incident_id}")
def get_incident(incident_id: str) -> dict[str, Any]:
    state = get_incident_state(incident_id)
    if state is None:
        raise HTTPException(status_code=404, detail="incident not found")

    incident: Incident = state["incident"]
    return {
        "incident": incident.model_dump(mode="json"),
        "requires_approval": state.get("requires_approval", False),
        "approved": state.get("approved"),
        "verification_passed": state.get("verification_passed"),
        "log": state.get("log", []),
    }


@app.post("/incidents/{incident_id}/approve")
def approve_incident(incident_id: str, background_tasks: BackgroundTasks) -> dict[str, str]:
    if get_incident_state(incident_id) is None:
        raise HTTPException(status_code=404, detail="incident not found")
    background_tasks.add_task(resume_incident, incident_id, True)
    return {"incident_id": incident_id, "status": "resuming"}


@app.post("/incidents/{incident_id}/deny")
def deny_incident(incident_id: str, background_tasks: BackgroundTasks) -> dict[str, str]:
    if get_incident_state(incident_id) is None:
        raise HTTPException(status_code=404, detail="incident not found")
    background_tasks.add_task(resume_incident, incident_id, False)
    return {"incident_id": incident_id, "status": "resuming"}
