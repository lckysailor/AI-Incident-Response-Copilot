from agentic_sre.agent.graph import (
    _route_after_approval,
    _route_after_plan,
    _route_after_verify,
)


def test_route_after_plan_auto_executes_when_no_approval_needed():
    assert _route_after_plan({"requires_approval": False}) == "execute_remediation"


def test_route_after_plan_requests_approval_when_flagged():
    assert _route_after_plan({"requires_approval": True}) == "request_approval"


def test_route_after_approval_proceeds_when_approved():
    assert _route_after_approval({"approved": True}) == "execute_remediation"


def test_route_after_approval_escalates_when_denied():
    assert _route_after_approval({"approved": False}) == "notify_resolution"


def test_route_after_verify_finishes_when_passed():
    state = {"verification_passed": True, "retries": 0, "max_retries": 2}
    assert _route_after_verify(state) == "generate_postmortem"


def test_route_after_verify_retries_diagnosis_when_under_budget():
    state = {"verification_passed": False, "retries": 0, "max_retries": 2}
    assert _route_after_verify(state) == "diagnose"


def test_route_after_verify_escalates_when_retries_exhausted():
    state = {"verification_passed": False, "retries": 2, "max_retries": 2}
    assert _route_after_verify(state) == "notify_resolution"
