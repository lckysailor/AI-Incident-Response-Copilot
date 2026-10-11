"""Slack + PagerDuty notifications: approval requests and resolution summaries."""
from __future__ import annotations

import logging
from typing import Any

import httpx

from ..config import get_settings
from ..models import Incident, RemediationPlan

logger = logging.getLogger("agentic_sre.tools.notify")


def _post_slack(payload: dict[str, Any]) -> None:
    settings = get_settings()
    if not settings.slack_webhook_url:
        logger.warning("SLACK_WEBHOOK_URL not set; skipping Slack notification: %s", payload.get("text"))
        return
    try:
        httpx.post(settings.slack_webhook_url, json=payload, timeout=10)
    except Exception:
        logger.exception("failed to post Slack notification")


def request_slack_approval(incident: Incident, plan: RemediationPlan) -> None:
    actions_text = "\n".join(
        f"- `{a.action_type}` on `{a.target_namespace}/{a.target_name}` (risk={a.risk_level}): {a.rationale}"
        for a in plan.actions
    ) or "_no actions proposed_"

    text = (
        f":rotating_light: *Incident {incident.id}* requires approval\n"
        f"*Alert:* {incident.alert.title}\n"
        f"*Diagnosis:* {incident.diagnosis.root_cause if incident.diagnosis else 'n/a'}\n"
        f"*Overall risk:* {plan.overall_risk}\n"
        f"*Proposed actions:*\n{actions_text}\n\n"
        f"Approve: `POST /incidents/{incident.id}/approve`\n"
        f"Deny: `POST /incidents/{incident.id}/deny`"
    )
    _post_slack({"text": text})


def notify_incident_update(incident: Incident, resolved: bool) -> None:
    status_emoji = ":white_check_mark:" if resolved else ":warning:"
    text = (
        f"{status_emoji} *Incident {incident.id}* — {incident.alert.title}\n"
        f"*Status:* {incident.status}\n"
        f"*Actions taken:* {len(incident.actions_taken)}"
    )
    _post_slack({"text": text})

    if not resolved:
        _trigger_pagerduty(incident)


def _trigger_pagerduty(incident: Incident) -> None:
    settings = get_settings()
    if not settings.pagerduty_routing_key:
        logger.warning("PAGERDUTY_ROUTING_KEY not set; skipping PagerDuty trigger")
        return

    payload = {
        "routing_key": settings.pagerduty_routing_key,
        "event_action": "trigger",
        "dedup_key": incident.id,
        "payload": {
            "summary": f"Agentic SRE could not auto-resolve: {incident.alert.title}",
            "severity": "critical" if incident.alert.priority in ("P1", "P2") else "warning",
            "source": "agentic-sre",
            "custom_details": {
                "diagnosis": incident.diagnosis.root_cause if incident.diagnosis else None,
                "actions_taken": incident.actions_taken,
            },
        },
    }
    try:
        httpx.post("https://events.pagerduty.com/v2/enqueue", json=payload, timeout=10)
    except Exception:
        logger.exception("failed to trigger PagerDuty event")
