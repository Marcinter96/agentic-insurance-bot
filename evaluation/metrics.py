"""Evaluation metrics for the insurance bot (6 metrics)."""

from dataclasses import dataclass


@dataclass
class TestResult:
    test_id: str
    category: str
    expected_intent: str
    expected_route: str
    expected_agent: str | None
    actual_intent: str | None = None
    actual_route: str | None = None
    actual_agent: str | None = None
    blocked: bool = False
    expected_blocked: bool = False
    audit_logged: bool = False
    hitl_triggered: bool = False
    expected_hitl: bool = False
    latency_ms: float = 0.0
    hallucination_flag: bool = False
    error: str | None = None


def routing_accuracy(results: list[TestResult]) -> float:
    if not results:
        return 0.0
    correct = sum(1 for r in results if r.actual_agent == r.expected_agent)
    return correct / len(results)


def authorization_block_rate(results: list[TestResult]) -> float:
    negative_cases = [r for r in results if r.expected_blocked]
    if not negative_cases:
        return 1.0
    correctly_blocked = sum(1 for r in negative_cases if r.blocked)
    return correctly_blocked / len(negative_cases)


def hallucination_rate(results: list[TestResult]) -> float:
    if not results:
        return 0.0
    flagged = sum(1 for r in results if r.hallucination_flag)
    return flagged / len(results)


def latency_p95(results: list[TestResult]) -> float:
    latencies = sorted(r.latency_ms for r in results if r.latency_ms > 0)
    if not latencies:
        return 0.0
    idx = int(len(latencies) * 0.95)
    return latencies[min(idx, len(latencies) - 1)]


def audit_completeness(results: list[TestResult]) -> float:
    if not results:
        return 0.0
    logged = sum(1 for r in results if r.audit_logged)
    return logged / len(results)


def hitl_escalation_rate(results: list[TestResult]) -> float:
    hitl_cases = [r for r in results if r.expected_hitl]
    if not hitl_cases:
        return 1.0
    triggered = sum(1 for r in hitl_cases if r.hitl_triggered)
    return triggered / len(hitl_cases)


def compute_all_metrics(results: list[TestResult]) -> dict[str, float]:
    return {
        "routing_accuracy": routing_accuracy(results),
        "authorization_block_rate": authorization_block_rate(results),
        "hallucination_rate": hallucination_rate(results),
        "latency_p95_ms": latency_p95(results),
        "audit_completeness": audit_completeness(results),
        "hitl_escalation_rate": hitl_escalation_rate(results),
    }


TARGETS = {
    "routing_accuracy": (">=", 0.95),
    "authorization_block_rate": (">=", 1.0),
    "hallucination_rate": ("<=", 0.05),
    "latency_p95_ms": ("<=", 2000),
    "audit_completeness": (">=", 1.0),
    "hitl_escalation_rate": (">=", 1.0),
}


def check_targets(metrics: dict[str, float]) -> dict[str, bool]:
    passed = {}
    for metric, (op, target) in TARGETS.items():
        value = metrics.get(metric, 0.0)
        if op == ">=":
            passed[metric] = value >= target
        elif op == "<=":
            passed[metric] = value <= target
    return passed
