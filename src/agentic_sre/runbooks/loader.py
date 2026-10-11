"""Loads YAML runbooks and matches them against an alert + diagnosis to
retrieve candidate remediation actions for the LLM planning node.

Keeping this retrieval step separate from the LLM keeps the actual action
catalog auditable and version-controlled: the LLM can refine wording/targets
but the space of *possible* actions for a known failure mode is defined here,
not hallucinated at runtime.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import yaml

from ..models import Alert, Diagnosis, RemediationAction

logger = logging.getLogger("agentic_sre.runbooks")

_LIBRARY_DIR = Path(__file__).parent / "library"


def _load_all_runbooks() -> list[dict]:
    runbooks = []
    if not _LIBRARY_DIR.exists():
        return runbooks
    for path in sorted(_LIBRARY_DIR.glob("*.yaml")):
        try:
            with open(path) as f:
                runbook = yaml.safe_load(f)
                runbook["_id"] = path.stem
                runbooks.append(runbook)
        except Exception:
            logger.exception("failed to load runbook %s", path)
    return runbooks


def match_runbooks(alert: Alert, diagnosis: Optional[Diagnosis]) -> list[RemediationAction]:
    """Returns candidate actions from any runbook whose match keywords appear in
    the alert title/tags or the diagnosed root cause."""
    haystack = " ".join(
        [alert.title, alert.message, " ".join(alert.tags), diagnosis.root_cause if diagnosis else ""]
    ).lower()

    candidates: list[RemediationAction] = []
    for runbook in _load_all_runbooks():
        keywords = [k.lower() for k in runbook.get("match_keywords", [])]
        if not any(keyword in haystack for keyword in keywords):
            continue
        namespace = alert.pod_or_deployment_tag("kube_namespace") or "default"
        deployment = alert.pod_or_deployment_tag("kube_deployment") or alert.service_tag() or "unknown"
        for action in runbook.get("suggested_actions", []):
            candidates.append(
                RemediationAction(
                    action_type=action["action_type"],
                    target_namespace=namespace,
                    target_name=deployment,
                    params=action.get("params", {}),
                    risk_level=action.get("risk_level", "medium"),
                    rationale=f"[runbook:{runbook['_id']}] {action.get('rationale', '')}",
                )
            )
    return candidates
