"""Node functions for the incident-response LangGraph.

Each function takes the current `IncidentState` and returns a partial update.
LLM calls are isolated behind `_call_llm_json` so the rest of the module reads
as plain control flow.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from ..config import get_settings
from ..models import Diagnosis, Incident, RemediationPlan
from ..otel.instrumentation import traced_node
from ..runbooks.loader import match_runbooks
from ..tools import datadog_tool, kubernetes_tool, notify_tool
from .prompts import (
    DIAGNOSIS_SYSTEM_PROMPT,
    DIAGNOSIS_USER_TEMPLATE,
    POSTMORTEM_SYSTEM_PROMPT,
    REMEDIATION_SYSTEM_PROMPT,
    REMEDIATION_USER_TEMPLATE,
)
from .state import IncidentState

logger = logging.getLogger("agentic_sre.agent")


def _get_llm():
    """Lazily construct the chat model so tests/imports don't require an API key."""
    from langchain_anthropic import ChatAnthropic

    settings = get_settings()
    return ChatAnthropic(
        model=settings.llm_model,
        api_key=settings.anthropic_api_key,
        temperature=0,
    )


def _call_llm_json(system_prompt: str, user_prompt: str) -> dict[str, Any]:
    """Invoke the LLM and parse its response as JSON, tolerating markdown fences."""
    llm = _get_llm()
    response = llm.invoke(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
    )
    text = response.content if isinstance(response.content, str) else str(response.content)
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[4:] if text.lower().startswith("json") else text
    return json.loads(text)


@traced_node("ingest_alert")
def ingest_alert(state: IncidentState) -> dict[str, Any]:
    incident: Incident = state["incident"]
    incident.status = "diagnosing"
    incident.touch()
    log = state.get("log", [])
    log.append(f"Ingested alert '{incident.alert.title}' (priority={incident.alert.priority})")
    return {"incident": incident, "log": log}


@traced_node("gather_telemetry")
def gather_telemetry(state: IncidentState) -> dict[str, Any]:
    incident: Incident = state["incident"]
    alert = incident.alert
    service = alert.service_tag()
    namespace = alert.pod_or_deployment_tag("kube_namespace") or get_settings().k8s_namespace
    deployment = alert.pod_or_deployment_tag("kube_deployment")

    snapshot = datadog_tool.gather_incident_telemetry(
        service=service, tags=alert.tags, alert_title=alert.title
    )
    if deployment:
        snapshot.k8s_pod_status = kubernetes_tool.get_pod_status(
            namespace=namespace, label_selector=f"app={deployment}"
        )
        snapshot.k8s_recent_events = kubernetes_tool.get_recent_events(namespace=namespace)

    incident.telemetry = snapshot
    incident.touch()
    log = state.get("log", [])
    log.append(
        f"Gathered telemetry: {len(snapshot.logs)} logs, {len(snapshot.traces)} traces, "
        f"{len(snapshot.events)} events, {len(snapshot.k8s_pod_status)} pods"
    )
    return {"incident": incident, "telemetry": snapshot, "log": log}


@traced_node("diagnose")
def diagnose(state: IncidentState) -> dict[str, Any]:
    incident: Incident = state["incident"]
    snapshot = state["telemetry"]
    alert = incident.alert
    log = state.get("log", [])

    user_prompt = DIAGNOSIS_USER_TEMPLATE.format(
        title=alert.title,
        priority=alert.priority,
        tags=alert.tags,
        message=alert.message,
        metrics=snapshot.metrics if snapshot else {},
        logs=(snapshot.logs if snapshot else [])[-20:],
        traces=(snapshot.traces if snapshot else [])[-10:],
        events=(snapshot.events if snapshot else [])[-10:],
        k8s_pod_status=snapshot.k8s_pod_status if snapshot else [],
        k8s_recent_events=snapshot.k8s_recent_events if snapshot else [],
    )

    try:
        raw = _call_llm_json(DIAGNOSIS_SYSTEM_PROMPT, user_prompt)
        diagnosis = Diagnosis(**raw)
    except Exception as exc:  # noqa: BLE001 - degrade gracefully, don't crash the run
        logger.exception("diagnosis failed")
        diagnosis = Diagnosis(
            root_cause="Diagnosis unavailable (LLM or parsing error); escalating to human.",
            confidence=0.0,
            evidence=[str(exc)],
            affected_resource=None,
        )

    incident.diagnosis = diagnosis
    incident.touch()
    log.append(f"Diagnosis (confidence={diagnosis.confidence:.2f}): {diagnosis.root_cause}")
    return {"incident": incident, "diagnosis": diagnosis, "log": log}


@traced_node("plan_remediation")
def plan_remediation(state: IncidentState) -> dict[str, Any]:
    incident: Incident = state["incident"]
    diagnosis = state["diagnosis"]
    settings = get_settings()
    log = state.get("log", [])

    candidates = match_runbooks(incident.alert, diagnosis)
    user_prompt = REMEDIATION_USER_TEMPLATE.format(
        root_cause=diagnosis.root_cause,
        confidence=diagnosis.confidence,
        evidence=diagnosis.evidence,
        affected_resource=diagnosis.affected_resource,
        candidates=json.dumps([c.model_dump() for c in candidates], default=str),
    )

    try:
        raw = _call_llm_json(REMEDIATION_SYSTEM_PROMPT, user_prompt)
        plan = RemediationPlan(**raw)
    except Exception as exc:  # noqa: BLE001
        logger.exception("remediation planning failed")
        plan = RemediationPlan(actions=[], overall_risk="high", runbook_matched=None)
        log.append(f"Remediation planning error, defaulting to manual escalation: {exc}")

    requires_approval = settings.risk_requires_approval(plan.overall_risk)
    incident.remediation_plan = plan
    incident.status = "awaiting_approval" if requires_approval else "planning"
    incident.touch()
    log.append(
        f"Remediation plan: {len(plan.actions)} action(s), overall_risk={plan.overall_risk}, "
        f"requires_approval={requires_approval}"
    )
    return {
        "incident": incident,
        "remediation_plan": plan,
        "requires_approval": requires_approval,
        "log": log,
    }


@traced_node("request_approval")
def request_approval(state: IncidentState) -> dict[str, Any]:
    """Posts to Slack and marks the state as pending; a human resumes the graph
    (e.g. via a `/incidents/{id}/approve` call) which sets `approved` before re-invoking."""
    incident: Incident = state["incident"]
    plan = state["remediation_plan"]
    log = state.get("log", [])

    notify_tool.request_slack_approval(incident, plan)
    incident.status = "awaiting_approval"
    incident.touch()
    log.append("Posted approval request to Slack; awaiting human response")
    return {"incident": incident, "log": log}


@traced_node("execute_remediation")
def execute_remediation(state: IncidentState) -> dict[str, Any]:
    incident: Incident = state["incident"]
    plan: RemediationPlan = state["remediation_plan"]
    log = state.get("log", [])
    incident.status = "remediating"

    results = []
    for action in plan.actions:
        result = kubernetes_tool.execute_action(action)
        results.append(result)
        log.append(f"Executed {action.action_type} on {action.target_name}: {result.get('status')}")

    incident.actions_taken.extend(results)
    incident.touch()
    return {"incident": incident, "log": log}


@traced_node("verify_fix")
def verify_fix(state: IncidentState) -> dict[str, Any]:
    incident: Incident = state["incident"]
    incident.status = "verifying"
    alert = incident.alert
    log = state.get("log", [])

    fresh_snapshot = datadog_tool.gather_incident_telemetry(
        service=alert.service_tag(), tags=alert.tags, alert_title=alert.title
    )
    passed = datadog_tool.telemetry_indicates_recovery(fresh_snapshot, baseline=state.get("telemetry"))
    incident.touch()
    log.append(f"Verification {'passed' if passed else 'failed'}")

    retries = state.get("retries", 0)
    if not passed:
        retries += 1
    return {
        "incident": incident,
        "verification_passed": passed,
        "telemetry": fresh_snapshot,
        "retries": retries,
        "log": log,
    }


@traced_node("notify_resolution")
def notify_resolution(state: IncidentState) -> dict[str, Any]:
    incident: Incident = state["incident"]
    passed = state.get("verification_passed")
    log = state.get("log", [])

    incident.status = "resolved" if passed else "escalated"
    incident.touch()
    notify_tool.notify_incident_update(incident, resolved=bool(passed))
    log.append(f"Notified stakeholders; final status={incident.status}")
    return {"incident": incident, "log": log}


@traced_node("generate_postmortem")
def generate_postmortem(state: IncidentState) -> dict[str, Any]:
    incident: Incident = state["incident"]
    log = state.get("log", [])

    record = {
        "alert": incident.alert.model_dump(mode="json"),
        "diagnosis": incident.diagnosis.model_dump() if incident.diagnosis else None,
        "remediation_plan": incident.remediation_plan.model_dump() if incident.remediation_plan else None,
        "actions_taken": incident.actions_taken,
        "status": incident.status,
    }
    try:
        llm = _get_llm()
        response = llm.invoke(
            [
                {"role": "system", "content": POSTMORTEM_SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(record, default=str)},
            ]
        )
        text = response.content if isinstance(response.content, str) else str(response.content)
    except Exception as exc:  # noqa: BLE001
        logger.exception("postmortem generation failed")
        text = f"_Postmortem generation failed: {exc}_"

    incident.postmortem_markdown = text
    incident.touch()
    log.append("Generated postmortem")
    return {"incident": incident, "log": log}
