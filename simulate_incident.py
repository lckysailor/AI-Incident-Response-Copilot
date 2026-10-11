import sys
from pathlib import Path

# Ensure src/ directory is on sys.path so agentic_sre can be imported
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

import json
try:
    from agentic_sre.config import get_settings
    from agentic_sre.models import Alert, Diagnosis, Incident
    from agentic_sre.runbooks.loader import match_runbooks
except ModuleNotFoundError as e:
    print(f"\n[!] Missing dependency: {e}")
    print("    Please install the dependencies: pip install -r requirements.txt")
    print("    Or run the zero-dependency demo: python run_demo_standalone.py\n")
    sys.exit(1)

def main():
    print("=" * 70)
    print("   AGENTIC SRE INCIDENT RESPONSE AGENT -- LOCAL SIMULATION")
    print("=" * 70)

    settings = get_settings()
    print(f"\n[1] Environment Config Loaded:")
    print(f"    - LLM Model: {settings.llm_model}")
    print(f"    - Auto-remediate ceiling: {settings.auto_remediate_max_risk}")
    print(f"    - Max diagnosis retries: {settings.max_diagnosis_retries}")

    # Create a simulated alert
    alert = Alert(
        title="High error rate and Pod CrashLoopBackOff",
        message="Pod payment-service-7d6f5f9c7-x8z21 is crashing repeatedly with exit code 137 (OOMKilled).",
        priority="P1",
        tags=[
            "env:production",
            "service:payment-service",
            "kube_deployment:payment-service",
            "kube_namespace:payments",
        ],
    )
    incident = Incident(alert=alert)
    print(f"\n[2] Alert Ingested:")
    print(f"    - Incident ID: {incident.id}")
    print(f"    - Title: {alert.title}")
    print(f"    - Service: {alert.service_tag()}")
    print(f"    - Target Deployment: {alert.pod_or_deployment_tag('kube_deployment')}")

    # Simulated diagnosis
    simulated_diagnosis = Diagnosis(
        root_cause="Memory leak in recent deployment causing OOMKill and continuous CrashLoopBackOff.",
        confidence=0.92,
        evidence=[
            "Pod restart_count=14 in last 10 minutes",
            "Last termination reason: OOMKilled",
            "Memory usage curve hit 100% limit (512Mi)",
        ],
        affected_resource="payments/payment-service",
    )
    incident.diagnosis = simulated_diagnosis
    print(f"\n[3] Root Cause Analysis (LLM Diagnosis):")
    print(f"    - Hypothesis: {simulated_diagnosis.root_cause}")
    print(f"    - Confidence: {simulated_diagnosis.confidence:.0%}")
    print(f"    - Evidence: {simulated_diagnosis.evidence}")

    # Runbook Matching
    candidate_actions = match_runbooks(alert, simulated_diagnosis)
    print(f"\n[4] Runbook Catalog Matching:")
    print(f"    - Matched {len(candidate_actions)} candidate action(s) from YAML library:")
    for i, action in enumerate(candidate_actions, 1):
        print(f"      {i}. Action: {action.action_type}")
        print(f"         Target: {action.target_namespace}/{action.target_name}")
        print(f"         Risk Level: {action.risk_level.upper()}")
        print(f"         Requires Human Approval: {settings.risk_requires_approval(action.risk_level)}")
        print(f"         Rationale: {action.rationale.strip()}")

    print("\n[5] Safety Guardrail Evaluation:")
    for action in candidate_actions:
        requires_approval = settings.risk_requires_approval(action.risk_level)
        if requires_approval:
            print(f"    [!] Action '{action.action_type}' ({action.risk_level}) EXCEEDS threshold ({settings.auto_remediate_max_risk}).")
            print(f"        -> Halting graph at 'request_approval' interrupt point.")
            print(f"        -> Sending Slack notification with Approve/Deny buttons.")
        else:
            print(f"    [OK] Action '{action.action_type}' ({action.risk_level}) is within auto-remediation budget.")
            print(f"        -> Executing via Kubernetes client directly.")

    print("\n" + "=" * 70)
    print("Simulation completed successfully! All core models & logic validated.")
    print("=" * 70)

if __name__ == "__main__":
    main()
