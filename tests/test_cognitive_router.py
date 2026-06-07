from ming.core.cognitive_router import CognitiveRouter, RoutingDecision
from ming.core.gate import Gate, GateDecision


def test_cognitive_router_routes_architecture_tasks_to_adversarial_mode():
    router = CognitiveRouter()

    decision = router.evaluate("请审查这个架构调整，并做 independent review")

    assert isinstance(decision, RoutingDecision)
    assert decision.is_adversarial
    assert any(rule.startswith("R2") or rule.startswith("R5") for rule in decision.triggered_rules)
    assert repr(decision).startswith("CognitiveRouter")


def test_legacy_gate_names_remain_compatible():
    assert Gate is CognitiveRouter
    assert GateDecision is RoutingDecision
