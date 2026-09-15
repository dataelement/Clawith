"""Validate the canonical Backend 50-Agent load-test profile.

Usage:
    uv run python scripts/validate_load_profile.py tests/performance/profiles/backend_50.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import NamedTuple

REFERENCE_PROFILE: dict[str, object] = {
    "schema_version": 1,
    "profile_id": "backend_50",
    "environment": {
        "cpu_vcpus": 8,
        "memory_gib": 16,
        "services": {
            "postgresql": "local_container",
            "redis": "local_container",
            "object_storage": "local_container",
        },
    },
    "duration": {
        "warmup_seconds": 180,
        "measurement_seconds": 900,
    },
    "provider": {
        "kind": "deterministic",
        "first_delta_ms": 100,
        "completion_ms": 500,
    },
    "tools": {
        "ordinary_io_latency_ms": 50,
        "slow_latency_ms": 2000,
    },
    "capacity": {
        "run_pool": 50,
        "admission_queue": 100,
        "database_pools": {
            "control": 20,
            "execution": 20,
        },
        "tool_concurrency": {
            "io": 32,
            "cpu": 4,
        },
    },
    "workload_mix": {
        "direct_session": 20,
        "group": 10,
        "subagent": 10,
        "heartbeat_or_trigger": 5,
        "a2a": 5,
    },
    "fixture_payload_bytes": {
        "session_input": 4096,
        "hot_context": 32768,
        "cold_context": 262144,
        "provider_delta": 1024,
        "provider_completion": 16384,
        "ordinary_tool_result": 16384,
        "slow_tool_result": 65536,
        "workspace_operation": 65536,
    },
    "thresholds": {
        "p95_ms": {
            "non_model_api": 500,
            "session_input_acceptance": 300,
            "hot_context_assembly": 200,
            "cold_context_assembly": 500,
            "bounded_workspace_operation": 500,
            "provider_delta_forwarding": 100,
        },
        "platform_error_rate_max_exclusive": 0.01,
        "accepted_durable_event_loss": 0,
        "stream_event_loss": 0,
    },
    "fairness": {
        "tenant_agent_admission": [
            "tenant_round_robin",
            "agent_round_robin",
        ],
        "per_agent_order": "fifo",
        "max_consecutive_skips_per_eligible_tenant": 1,
    },
}


class ValidationIssue(NamedTuple):
    path: str
    code: str
    message: str


def _child_path(parent: str, child: str) -> str:
    return f"{parent}.{child}" if parent else child


def _validate_value(
    actual: object,
    expected: object,
    path: str,
) -> tuple[ValidationIssue, ...]:
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return (ValidationIssue(path, "invalid", "must be an object"),)

        issues: list[ValidationIssue] = []
        for key in expected.keys() - actual.keys():
            issues.append(
                ValidationIssue(
                    _child_path(path, key),
                    "missing",
                    "required field is missing",
                )
            )
        for key in actual.keys() - expected.keys():
            issues.append(
                ValidationIssue(
                    _child_path(path, key),
                    "unknown",
                    "field is not part of the approved profile",
                )
            )
        for key in expected.keys() & actual.keys():
            issues.extend(
                _validate_value(
                    actual[key],
                    expected[key],
                    _child_path(path, key),
                )
            )
        return tuple(issues)

    if type(actual) is not type(expected) or actual != expected:
        return (
            ValidationIssue(
                path,
                "invalid",
                f"must equal the approved value {expected!r}",
            ),
        )
    return ()


def validate_profile(profile: object) -> tuple[ValidationIssue, ...]:
    """Return every field-level deviation from the approved reference profile."""

    return tuple(sorted(_validate_value(profile, REFERENCE_PROFILE, "")))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile", type=Path, help="Path to the load profile JSON")
    args = parser.parse_args(argv)

    try:
        profile = json.loads(args.profile.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"load profile could not be read: {exc}", file=sys.stderr)
        return 2

    issues = validate_profile(profile)
    if issues:
        for issue in issues:
            print(f"{issue.code}: {issue.path or '<root>'}: {issue.message}", file=sys.stderr)
        return 1

    print(f"load profile is valid: {args.profile}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
