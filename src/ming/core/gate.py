"""Backward-compatible imports for the old Gate module name."""

from ming.core.cognitive_router import CognitiveRouter, RoutingDecision

Gate = CognitiveRouter
GateDecision = RoutingDecision

__all__ = ["Gate", "GateDecision", "CognitiveRouter", "RoutingDecision"]
