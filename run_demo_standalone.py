"""Standalone, zero-dependency interactive demonstration of the Agentic SRE Incident Responder.
Works with 100% standard library Python (no pip install required to run this demo).

Simulates an incident on Gnani.ai's production Voice Streaming ASR microservice.
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Optional

# --- DOMAIN MODELS (Standard Library Dataclasses) ---

RiskLevel = Literal["low", "medium", "high"]

@dataclass
class Alert:
    title: str
    message: str
    priority: str = "P1"
    tags: list[str] = field(default_factory=list)
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    source: str = "datadog"

    def service_tag(self) -> Optional[str]:
        for tag in self.tags:
            if tag.startswith("service:"):
                return tag.split(":", 1)[1]
        return None

    def deployment_tag(self) -> Optional[str]:
        for tag in self.tags:
            if tag.startswith("kube_deployment:"):
                return tag.split(":", 1)[1]
        return None

@dataclass
class TelemetrySnapshot:
    metrics: dict[str, Any]
    logs: list[str]
    k8s_pod_status: list[dict[str, Any]]
    k8s_recent_events: list[dict[str, Any]]

@dataclass
class Diagnosis:
    root_cause: str
    confidence: float
    evidence: list[str]
    affected_resource: str

@dataclass
class RemediationAction:
    action_type: str
    target_namespace: str
    target_name: str
    risk_level: RiskLevel
    rationale: str
    params: dict[str, Any] = field(default_factory=dict)

@dataclass
class RemediationPlan:
    actions: list[RemediationAction]
    overall_risk: RiskLevel
    runbook_matched: Optional[str]

# --- SIMPLE YAML PARSER (Standard Library Fallback) ---

def parse_simple_yaml(text: str) -> dict[str, Any]:
    """Lightweight YAML parser for demo purposes without external pyyaml dependency."""
    try:
        import yaml
        return yaml.safe_load(text)
    except ImportError:
        # Fallback parser for the runbook schema
        data: dict[str, Any] = {"match_keywords": [], "suggested_actions": []}
        current_section = None
        current_action: dict[str, Any] = {}
        
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if stripped.startswith("name:"):
                data["name"] = stripped.split("name:", 1)[1].strip()
            elif stripped.startswith("match_keywords:"):
                current_section = "keywords"
            elif stripped.startswith("suggested_actions:"):
                current_section = "actions"
            elif current_section == "keywords" and stripped.startswith("- "):
                data["match_keywords"].append(stripped[2:].strip())
            elif current_section == "actions":
                if stripped.startswith("- action_type:"):
                    if current_action:
                        data["suggested_actions"].append(current_action)
                    current_action = {"action_type": stripped.split(":", 1)[1].strip()}
                elif ":" in stripped and current_action:
                    k, v = stripped.split(":", 1)
                    current_action[k.strip()] = v.strip().strip('"').strip("'")
        if current_action:
            data["suggested_actions"].append(current_action)
        return data

def load_runbooks_demo() -> list[dict[str, Any]]:
    runbooks = []
    lib_path = Path(__file__).resolve().parent / "src" / "agentic_sre" / "runbooks" / "library"
    if lib_path.exists():
        for f in lib_path.glob("*.yaml"):
            content = f.read_text(encoding="utf-8")
            rb = parse_simple_yaml(content)
            rb["_id"] = f.stem
            runbooks.append(rb)
    return runbooks

def match_runbooks_demo(alert: Alert, diagnosis: Diagnosis) -> list[RemediationAction]:
    haystack = f"{alert.title} {alert.message} {' '.join(alert.tags)} {diagnosis.root_cause}".lower()
    candidates = []
    for rb in load_runbooks_demo():
        keywords = [k.lower() for k in rb.get("match_keywords", [])]
        if any(k in haystack for k in keywords):
            ns = "prod-voice"
            dep = alert.deployment_tag() or alert.service_tag() or "voice-service"
            for action in rb.get("suggested_actions", []):
                candidates.append(
                    RemediationAction(
                        action_type=action.get("action_type", "restart_deployment"),
                        target_namespace=ns,
                        target_name=dep,
                        risk_level=action.get("risk_level", "low"),
                        rationale=f"[Runbook: {rb.get('_id')}] {action.get('rationale', '')}",
                    )
                )
    return candidates

# --- SIMULATION PIPELINE ---

def simulate_pipeline():
    print("=" * 80)
    print("   GNANI.AI SRE INCIDENT RESPONSE AGENT -- LIVE PIPELINE SIMULATION")
    print("=" * 80)

    # Step 1: Alert Ingestion
    print("\n[STEP 1: INGESTION] Inbound Datadog Webhook received...")
    alert = Alert(
        title="High Latency & Pod CrashLoopBackOff on asr-streaming-engine",
        message="p99 audio stream latency spiked to 920ms (SLO threshold: 200ms). Pod asr-streaming-engine-8f7d is crashlooping with OOMKilled.",
        priority="P1",
        tags=[
            "env:production",
            "team:voice-ai",
            "service:asr-streaming-engine",
            "kube_deployment:asr-streaming-engine",
            "kube_namespace:prod-voice",
        ],
    )
    time.sleep(0.6)
    print(f"  -> Incident ID : {alert.id}")
    print(f"  -> Service      : {alert.service_tag()}")
    print(f"  -> Deployment   : {alert.deployment_tag()}")
    print(f"  -> Severity     : {alert.priority} (CRITICAL)")

    # Step 2: Telemetry Gathering
    print("\n[STEP 2: TELEMETRY] Correlating Datadog Metrics, APM Traces & K8s Events...")
    time.sleep(0.8)
    snapshot = TelemetrySnapshot(
        metrics={"p99_latency_ms": 920.4, "error_rate": 0.14, "cpu_pct": 98.2, "mem_pct": 100.0},
        logs=[
            "[ERROR] WebSocket audio buffer overflow on thread-44",
            "[FATAL] java.lang.OutOfMemoryError: Java heap space during beam search",
            "[WARN] Healthcheck failed: http://10.244.1.18:8080/healthz returned 503",
        ],
        k8s_pod_status=[
            {"pod": "asr-streaming-engine-8f7d-x1", "status": "CrashLoopBackOff", "restarts": 12},
            {"pod": "asr-streaming-engine-8f7d-x2", "status": "Running", "restarts": 0},
        ],
        k8s_recent_events=[
            {"reason": "OOMKilled", "message": "Container asr exceeded 4Gi memory limit", "count": 12},
            {"reason": "Unhealthy", "message": "Liveness probe failed: connection refused", "count": 5},
        ]
    )
    print(f"  -> Datadog Metrics : p99={snapshot.metrics['p99_latency_ms']}ms | CPU={snapshot.metrics['cpu_pct']}% | ErrorRate={snapshot.metrics['error_rate'] * 100}%")
    print(f"  -> Pod Health      : Pod asr-streaming-engine-8f7d-x1 is in CrashLoopBackOff (12 restarts)")
    print(f"  -> Cluster Events  : Caught {snapshot.k8s_recent_events[0]['reason']} - {snapshot.k8s_recent_events[0]['message']}")

    # Step 3: LLM Diagnosis
    print("\n[STEP 3: DIAGNOSE] LLM Synthesizing Root Cause Hypothesis...")
    time.sleep(1.0)
    diagnosis = Diagnosis(
        root_cause="Audio stream buffer leak during high-concurrency telephony load causing pod OOMKill and cascade latency degradation.",
        confidence=0.96,
        evidence=[
            "Pod container killed with exit code 137 (OOMKilled limit: 4Gi)",
            "Last log indicates buffer overflow during acoustic model beam search",
            "p99 latency degraded from normal 120ms to 920ms on remaining single pod",
        ],
        affected_resource="prod-voice/asr-streaming-engine",
    )
    print(f"  -> Hypothesis : {diagnosis.root_cause}")
    print(f"  -> Confidence : {diagnosis.confidence:.0%}")
    print(f"  -> Evidence   :")
    for ev in diagnosis.evidence:
        print(f"       * {ev}")

    # Step 4: Runbook Matching
    print("\n[STEP 4: RUNBOOK MATCHING] Querying version-controlled YAML runbooks...")
    time.sleep(0.6)
    candidates = match_runbooks_demo(alert, diagnosis)
    print(f"  -> Matched {len(candidates)} candidate actions from repository runbooks:")
    for i, c in enumerate(candidates, 1):
        print(f"     {i}. Action: {c.action_type:<22} | Target: {c.target_namespace}/{c.target_name} | Risk: {c.risk_level.upper()}")
        print(f"        Rationale: {c.rationale}")

    # Step 5: Guardrail Evaluation
    print("\n[STEP 5: SAFETY GUARDRAIL GATE] Evaluating auto-remediation ceiling (MAX_RISK=low)...")
    time.sleep(0.6)
    
    selected_action = candidates[0] # restart_deployment (low risk)
    high_risk_action = RemediationAction(
        action_type="rollback_deployment",
        target_namespace="prod-voice",
        target_name="asr-streaming-engine",
        risk_level="high",
        rationale="Roll back to previous acoustic model revision",
    )

    print(f"  -> Scenario A: Selected action '{selected_action.action_type}' (Risk: {selected_action.risk_level.upper()})")
    print(f"     Status: Risk is within 'low' ceiling -> AUTO-APPROVED. Executing directly.")

    print(f"\n  -> Scenario B: If planning suggested '{high_risk_action.action_type}' (Risk: {high_risk_action.risk_level.upper()})")
    print(f"     Status: Risk EXCEEDS auto-remediation ceiling!")
    print(f"     [!] LangGraph enters 'interrupt_after' state.")
    print(f"     [!] State saved to checkpointer (MemorySaver).")
    print(f"     [!] Posted to Slack: '🚨 Approval required for rollback on asr-streaming-engine'")
    print(f"     [!] Waiting for SRE click: POST /incidents/{alert.id}/approve")

    # Step 6: Execution Simulation
    print("\n[STEP 6: EXECUTE] Invoking Kubernetes API (least-privilege patch)...")
    time.sleep(0.8)
    print(f"  -> kubectl patch deployment asr-streaming-engine -n prod-voice")
    print(f"  -> Added annotation: agentic-sre/restartedAt={datetime.now(timezone.utc).isoformat()}")
    print(f"  -> Deployment rollout started: terminating bad pod, spinning up clean instance.")

    # Step 7: Closed-Loop Verification
    print("\n[STEP 7: VERIFY] Waiting cooldown period & re-querying Datadog telemetry...")
    time.sleep(1.2)
    post_remediation_p99 = 118.0
    post_remediation_errors = 0
    post_remediation_status = "Running"
    
    print(f"  -> Fresh Datadog p99 latency : {post_remediation_p99}ms (SLO restored: < 200ms)")
    print(f"  -> Error log count           : {post_remediation_errors}")
    print(f"  -> Pod status                : {post_remediation_status}")
    print(f"  -> Verification Result       : [PASSED] (Recovery heuristic confirmed)")

    # Step 8: Postmortem
    print("\n[STEP 8: POSTMORTEM] Generating blameless incident summary...")
    time.sleep(0.6)
    postmortem = f"""
## Incident Postmortem: {alert.title}
- **Date/Time**: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}
- **Service Affected**: `asr-streaming-engine` (prod-voice)
- **MTTR**: ~45 seconds (Automated by Agentic SRE)
- **Root Cause**: Audio buffer accumulation under high concurrency caused memory spike, triggering K8s OOMKill.
- **Resolution**: Autonomous rolling restart cleared stuck memory pools. Pod restored to healthy status, p99 latency dropped from 920ms to 118ms.
- **Follow-up**: SRE team to review JVM heap limits and audio streaming connection pool timeout in Sprint 42.
"""
    print(postmortem.strip())
    print("\n" + "=" * 80)
    print("   [SUCCESS] Full incident lifecycle completed autonomously and verified safe!")
    print("=" * 80)

if __name__ == "__main__":
    simulate_pipeline()
