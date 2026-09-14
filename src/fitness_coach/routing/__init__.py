"""Deterministic response routing: decide which information sources a reply needs."""

from fitness_coach.routing.classifier import RouteCategory, RoutingDecision, classify_message

__all__ = ["RouteCategory", "RoutingDecision", "classify_message"]
