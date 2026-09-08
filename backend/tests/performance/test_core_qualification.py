"""Pure qualification policy checks; synthetic reports are not load evidence."""

import json
from copy import deepcopy

import pytest
from performance.test_core_load import CORE_LATENCY_THRESHOLDS, PROFILE, qualify_core


@pytest.fixture
def reference():
    profile = json.loads(PROFILE.read_text())
    report = {
        "environment": {"backend_cpu_vcpus": 8, "backend_memory_bytes": 16 * 1024**3,
            "docker_cpu_vcpus": 8, "docker_memory_bytes": 16 * 1024**3,
            "postgresql": "local_container", "redis": "not_used_by_core", "object_storage": "local_container"},
        "durations_seconds": {"warmup": 180.01, "measurement": 900.01},
        "agent_count": 50, "offered_client_lanes": 50, "runtime_capacity": deepcopy(profile["capacity"]),
        "payload_targets": deepcopy(profile["fixture_payload_bytes"]),
        "metrics": {name: {"count": 1000, "p95_ms": profile["thresholds"]["p95_ms"][threshold]}
            for name, threshold in CORE_LATENCY_THRESHOLDS.items()},
        "accepted_runs": 1000, "completed_runs": 1000, "failed_runs": 0,
        "platform_failed_runs": 0, "runs_with_failed_or_missing_tool_result": 0,
        "platform_error_rate": 0, "accepted_durable_event_loss": 0, "stream_event_loss": 0,
        "max_active_slots_observed": 50, "sample_overflow": False,
        "workload_measurements": {"slow_io": {"count": 100, "latency_ms": 2000, "payload_bytes": 65536},
            "cpu": {"count": 100, "max_concurrency": 4}},
        "unmeasured": ["session_input_acceptance", "non_model_api", "g006_product_workload_mix",
            "hostile_fairness_during_load"],
    }
    return profile, report


def test_complete_reference_measurements_can_qualify_without_g006_apis(reference):
    profile, report = reference
    before = deepcopy(report)
    assert qualify_core(report, profile) == ("qualified", [])
    assert report == before


@pytest.mark.parametrize("metric", tuple(CORE_LATENCY_THRESHOLDS))
@pytest.mark.parametrize("defect", ["missing", "no_samples", "unknown_p95", "over_threshold", "nan", "boolean"])
def test_each_required_latency_needs_real_in_bound_samples(reference, metric, defect):
    profile, report = reference
    sample = report["metrics"][metric]
    if defect == "missing":
        del report["metrics"][metric]
    elif defect == "no_samples":
        sample["count"] = 0
    elif defect == "unknown_p95":
        sample["p95_ms"] = None
    elif defect == "over_threshold":
        sample["p95_ms"] += 1
    elif defect == "nan":
        sample["p95_ms"] = float("nan")
    else:
        sample["count"] = True
    status, reasons = qualify_core(report, profile)
    assert status == "not_qualified" and any(metric in reason for reason in reasons)


@pytest.mark.parametrize("phase,target", [("warmup", 180), ("measurement", 900)])
@pytest.mark.parametrize("delta", [-.001, 1.01])
def test_actual_phase_duration_cannot_be_shortened_or_overrun(reference, phase, target, delta):
    profile, report = reference
    report["durations_seconds"][phase] = target + delta
    status, reasons = qualify_core(report, profile)
    assert status == "not_qualified" and any(phase in reason for reason in reasons)


@pytest.mark.parametrize("field,value", [("backend_cpu_vcpus", 4), ("backend_memory_bytes", 8 * 1024**3),
    ("docker_cpu_vcpus", 4), ("docker_memory_bytes", 8 * 1024**3),
    ("postgresql", "remote"), ("object_storage", "local_filesystem"), ("redis", None)])
def test_wrong_or_missing_environment_cannot_qualify(reference, field, value):
    profile, report = reference
    report["environment"][field] = value
    assert qualify_core(report, profile)[0] == "not_qualified"


@pytest.mark.parametrize("field,value", [("agent_count", 49), ("runtime_capacity", {}), ("payload_targets", {}),
    ("accepted_runs", 0), ("completed_runs", 999), ("failed_runs", None), ("platform_failed_runs", None),
    ("runs_with_failed_or_missing_tool_result", 1), ("platform_error_rate", None),
    ("accepted_durable_event_loss", 1), ("stream_event_loss", 1),
    ("max_active_slots_observed", 1), ("max_active_slots_observed", 49),
    ("max_active_slots_observed", 51), ("max_active_slots_observed", None),
    ("offered_client_lanes", 1), ("offered_client_lanes", 49), ("offered_client_lanes", None),
    ("sample_overflow", True), ("sample_overflow", None), ("workload_measurements", {})])
def test_missing_or_invalid_capacity_outcomes_and_workloads_fail(reference, field, value):
    profile, report = reference
    report[field] = value
    assert qualify_core(report, profile)[0] == "not_qualified"


@pytest.mark.parametrize("errors,expected", [(9, "qualified"), (10, "not_qualified")])
def test_platform_error_rate_uses_exclusive_threshold_not_zero_failure_policy(reference, errors, expected):
    profile, report = reference
    report.update(completed_runs=1000 - errors, failed_runs=errors,
        platform_failed_runs=errors, platform_error_rate=errors / 1000)
    assert qualify_core(report, profile)[0] == expected


def test_reported_error_rate_cannot_hide_actual_failures(reference):
    profile, report = reference
    report.update(completed_runs=900, failed_runs=100, platform_failed_runs=100, platform_error_rate=0)
    assert qualify_core(report, profile)[0] == "not_qualified"


@pytest.mark.parametrize("workload,field,value", [("slow_io", "count", 0), ("slow_io", "latency_ms", 50),
    ("slow_io", "payload_bytes", 16384), ("cpu", "count", 0), ("cpu", "max_concurrency", 5)])
def test_required_core_workloads_cannot_be_omitted_or_weakened(reference, workload, field, value):
    profile, report = reference
    report["workload_measurements"][workload][field] = value
    assert qualify_core(report, profile)[0] == "not_qualified"


def test_smoke_never_qualifies_even_with_reference_shaped_data(reference):
    profile, report = reference
    report["smoke"] = True
    assert qualify_core(report, profile)[0] == "smoke_only"


def test_empty_report_does_not_invent_successful_zero_measurements(reference):
    profile, _ = reference
    assert qualify_core({}, profile)[0] == "not_qualified"
