from agentic_sre.models import RemediationAction
from agentic_sre.tools import kubernetes_tool


def test_execute_action_skips_no_action():
    action = RemediationAction(
        action_type="no_action",
        target_namespace="default",
        target_name="checkout",
        risk_level="low",
        rationale="nothing to do",
    )
    result = kubernetes_tool.execute_action(action)
    assert result["status"] == "skipped"


def test_execute_action_dispatches_to_restart(monkeypatch):
    called = {}

    def fake_restart(namespace, name):
        called["args"] = (namespace, name)
        return {"action": "restart_deployment", "target": f"{namespace}/{name}", "status": "ok"}

    monkeypatch.setattr(kubernetes_tool, "restart_deployment", fake_restart)
    monkeypatch.setitem(
        kubernetes_tool._ACTION_DISPATCH,
        "restart_deployment",
        lambda a: fake_restart(a.target_namespace, a.target_name),
    )

    action = RemediationAction(
        action_type="restart_deployment",
        target_namespace="prod",
        target_name="checkout",
        risk_level="low",
        rationale="crash loop",
    )
    result = kubernetes_tool.execute_action(action)
    assert result["status"] == "ok"
    assert called["args"] == ("prod", "checkout")


def test_execute_action_handles_unknown_type():
    action = RemediationAction.model_construct(
        action_type="delete_everything",
        target_namespace="prod",
        target_name="checkout",
        params={},
        risk_level="high",
        rationale="nope",
    )
    result = kubernetes_tool.execute_action(action)
    assert result["status"] == "error"


def test_execute_action_catches_handler_exceptions(monkeypatch):
    def boom(a):
        raise RuntimeError("cluster unreachable")

    monkeypatch.setitem(kubernetes_tool._ACTION_DISPATCH, "scale_deployment", boom)
    action = RemediationAction(
        action_type="scale_deployment",
        target_namespace="prod",
        target_name="checkout",
        params={"replicas": 3},
        risk_level="medium",
        rationale="load",
    )
    result = kubernetes_tool.execute_action(action)
    assert result["status"] == "error"
    assert "cluster unreachable" in result["error"]
