"""Builds and exposes the incident-response LangGraph.

The graph pauses (via LangGraph's checkpointer + `interrupt_after`) whenever a
remediation plan needs human approval. `run_incident` starts a new thread;
`resume_incident` is called by the API layer once a human has approved or
denied the pending action.
"""
from __future__ import annotations

from typing import Literal, Optional

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from ..models import Incident
from . import nodes
from .state import IncidentState

_MAX_RETRIES_DEFAULT = 2


def _route_after_plan(state: IncidentState) -> Literal["request_approval", "execute_remediation"]:
    return "request_approval" if state.get("requires_approval") else "execute_remediation"


def _route_after_approval(state: IncidentState) -> Literal["execute_remediation", "notify_resolution"]:
    return "execute_remediation" if state.get("approved") else "notify_resolution"


def _route_after_verify(state: IncidentState) -> Literal["generate_postmortem", "diagnose", "notify_resolution"]:
    if state.get("verification_passed"):
        return "generate_postmortem"
    retries = state.get("retries", 0)
    max_retries = state.get("max_retries", _MAX_RETRIES_DEFAULT)
    if retries < max_retries:
        return "diagnose"
    return "notify_resolution"


def build_graph():
    graph = StateGraph(IncidentState)

    graph.add_node("ingest_alert", nodes.ingest_alert)
    graph.add_node("gather_telemetry", nodes.gather_telemetry)
    graph.add_node("diagnose", nodes.diagnose)
    graph.add_node("plan_remediation", nodes.plan_remediation)
    graph.add_node("request_approval", nodes.request_approval)
    graph.add_node("execute_remediation", nodes.execute_remediation)
    graph.add_node("verify_fix", nodes.verify_fix)
    graph.add_node("notify_resolution", nodes.notify_resolution)
    graph.add_node("generate_postmortem", nodes.generate_postmortem)

    graph.add_edge(START, "ingest_alert")
    graph.add_edge("ingest_alert", "gather_telemetry")
    graph.add_edge("gather_telemetry", "diagnose")
    graph.add_edge("diagnose", "plan_remediation")
    graph.add_conditional_edges("plan_remediation", _route_after_plan)
    graph.add_conditional_edges("request_approval", _route_after_approval)
    graph.add_edge("execute_remediation", "verify_fix")
    graph.add_conditional_edges("verify_fix", _route_after_verify)
    graph.add_edge("notify_resolution", "generate_postmortem")
    graph.add_edge("generate_postmortem", END)

    return graph


_checkpointer = MemorySaver()
_compiled_app = build_graph().compile(checkpointer=_checkpointer, interrupt_after=["request_approval"])


def get_app():
    """Returns the compiled, checkpointed graph (singleton in-process)."""
    return _compiled_app


def run_incident(incident: Incident, max_retries: int = _MAX_RETRIES_DEFAULT) -> IncidentState:
    """Starts a fresh graph run for a brand-new incident. Blocks until the graph
    either finishes or pauses at the human-approval interrupt point."""
    app = get_app()
    config = {"configurable": {"thread_id": incident.id}}
    initial_state: IncidentState = {
        "incident": incident,
        "retries": 0,
        "max_retries": max_retries,
        "log": [],
    }
    return app.invoke(initial_state, config)


def resume_incident(incident_id: str, approved: bool) -> IncidentState:
    """Resumes a paused (awaiting-approval) incident run after a human decision."""
    app = get_app()
    config = {"configurable": {"thread_id": incident_id}}
    app.update_state(config, {"approved": approved})
    return app.invoke(None, config)


def get_incident_state(incident_id: str) -> Optional[IncidentState]:
    app = get_app()
    config = {"configurable": {"thread_id": incident_id}}
    snapshot = app.get_state(config)
    return snapshot.values if snapshot else None
