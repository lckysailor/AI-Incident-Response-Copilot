"""Kubernetes integration: read-only status/events plus a small, deliberately
narrow set of remediation actions (restart, scale, rollback).

Uses the official `kubernetes` Python client. Cluster credentials come from
either in-cluster config (when running as a Pod, `K8S_IN_CLUSTER=true`) or a
kubeconfig file for local/dev use.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from ..config import get_settings
from ..models import RemediationAction

logger = logging.getLogger("agentic_sre.tools.kubernetes")


def _load_config() -> None:
    from kubernetes import config as k8s_config

    settings = get_settings()
    if settings.k8s_in_cluster:
        k8s_config.load_incluster_config()
    else:
        k8s_config.load_kube_config(config_file=settings.kubeconfig)


def _apps_v1():
    from kubernetes import client

    _load_config()
    return client.AppsV1Api()


def _core_v1():
    from kubernetes import client

    _load_config()
    return client.CoreV1Api()


def get_pod_status(namespace: str, label_selector: str) -> list[dict[str, Any]]:
    try:
        pods = _core_v1().list_namespaced_pod(namespace=namespace, label_selector=label_selector)
    except Exception:
        logger.exception("failed to list pods in %s (%s)", namespace, label_selector)
        return []

    results = []
    for pod in pods.items:
        container_statuses = pod.status.container_statuses or []
        waiting_reasons = [
            cs.state.waiting.reason for cs in container_statuses if cs.state and cs.state.waiting
        ]
        results.append(
            {
                "name": pod.metadata.name,
                "phase": pod.status.phase,
                "restart_count": sum(cs.restart_count for cs in container_statuses) if container_statuses else 0,
                "status": ",".join(waiting_reasons) if waiting_reasons else pod.status.phase,
            }
        )
    return results


def get_recent_events(namespace: str, limit: int = 20) -> list[dict[str, Any]]:
    try:
        events = _core_v1().list_namespaced_event(namespace=namespace)
    except Exception:
        logger.exception("failed to list events in %s", namespace)
        return []

    items = sorted(
        events.items,
        key=lambda e: e.last_timestamp or e.event_time or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )[:limit]
    return [
        {
            "reason": e.reason,
            "message": e.message,
            "object": f"{e.involved_object.kind}/{e.involved_object.name}",
            "type": e.type,
            "count": e.count,
        }
        for e in items
    ]


def restart_deployment(namespace: str, name: str) -> dict[str, Any]:
    """Equivalent of `kubectl rollout restart deployment/<name>`: patches a
    timestamp annotation on the pod template so Kubernetes rolls new pods."""
    from kubernetes import client

    patch = {
        "spec": {
            "template": {
                "metadata": {
                    "annotations": {
                        "agentic-sre/restartedAt": datetime.now(timezone.utc).isoformat()
                    }
                }
            }
        }
    }
    try:
        _apps_v1().patch_namespaced_deployment(name=name, namespace=namespace, body=patch)
        return {"action": "restart_deployment", "target": f"{namespace}/{name}", "status": "ok"}
    except client.ApiException as exc:
        logger.exception("restart_deployment failed for %s/%s", namespace, name)
        return {"action": "restart_deployment", "target": f"{namespace}/{name}", "status": "error", "error": str(exc)}


def scale_deployment(namespace: str, name: str, replicas: int) -> dict[str, Any]:
    from kubernetes import client

    patch = {"spec": {"replicas": replicas}}
    try:
        _apps_v1().patch_namespaced_deployment_scale(name=name, namespace=namespace, body=patch)
        return {
            "action": "scale_deployment",
            "target": f"{namespace}/{name}",
            "status": "ok",
            "replicas": replicas,
        }
    except client.ApiException as exc:
        logger.exception("scale_deployment failed for %s/%s", namespace, name)
        return {"action": "scale_deployment", "target": f"{namespace}/{name}", "status": "error", "error": str(exc)}


def rollback_deployment(namespace: str, name: str) -> dict[str, Any]:
    """Best-effort equivalent of `kubectl rollout undo`: finds the previous
    ReplicaSet revision owned by this Deployment and re-applies its pod template."""
    from kubernetes import client

    apps = _apps_v1()
    try:
        deployment = apps.read_namespaced_deployment(name=name, namespace=namespace)
        owner_uid = deployment.metadata.uid
        replica_sets = apps.list_namespaced_replica_set(namespace=namespace)
        owned = [
            rs
            for rs in replica_sets.items
            if rs.metadata.owner_references
            and any(ref.uid == owner_uid for ref in rs.metadata.owner_references)
        ]
        owned.sort(
            key=lambda rs: int(rs.metadata.annotations.get("deployment.kubernetes.io/revision", 0)),
            reverse=True,
        )
        if len(owned) < 2:
            return {
                "action": "rollback_deployment",
                "target": f"{namespace}/{name}",
                "status": "error",
                "error": "no previous revision found",
            }
        previous = owned[1]
        patch = {"spec": {"template": previous.spec.template.to_dict()}}
        apps.patch_namespaced_deployment(name=name, namespace=namespace, body=patch)
        return {"action": "rollback_deployment", "target": f"{namespace}/{name}", "status": "ok"}
    except client.ApiException as exc:
        logger.exception("rollback_deployment failed for %s/%s", namespace, name)
        return {"action": "rollback_deployment", "target": f"{namespace}/{name}", "status": "error", "error": str(exc)}


_ACTION_DISPATCH = {
    "restart_deployment": lambda a: restart_deployment(a.target_namespace, a.target_name),
    "scale_deployment": lambda a: scale_deployment(
        a.target_namespace, a.target_name, a.params.get("replicas", 1)
    ),
    "rollback_deployment": lambda a: rollback_deployment(a.target_namespace, a.target_name),
}


def execute_action(action: RemediationAction) -> dict[str, Any]:
    if action.action_type in ("no_action", "manual"):
        return {
            "action": action.action_type,
            "target": f"{action.target_namespace}/{action.target_name}",
            "status": "skipped",
        }
    handler = _ACTION_DISPATCH.get(action.action_type)
    if handler is None:
        return {
            "action": action.action_type,
            "target": f"{action.target_namespace}/{action.target_name}",
            "status": "error",
            "error": "unknown action_type",
        }
    try:
        return handler(action)
    except Exception as exc:  # noqa: BLE001 - never let a remediation crash the graph
        logger.exception("execute_action failed for %s", action.action_type)
        return {
            "action": action.action_type,
            "target": f"{action.target_namespace}/{action.target_name}",
            "status": "error",
            "error": str(exc),
        }
