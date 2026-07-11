from copy import deepcopy

import pytest

from memwiki.coordinator_models import CoordinatorRuntimeState, validate_slice_transition


def valid_state() -> dict[str, object]:
    return {
        "schema_version": 1,
        "run_id": "run-001",
        "revision": 3,
        "status": "active",
        "created_at": "2026-07-11T10:00:00-04:00",
        "updated_at": "2026-07-11T10:05:00-04:00",
        "max_workers": 6,
        "slices": [
            {
                "slice_id": "slice-a",
                "title": "Foundation",
                "status": "integrated",
                "depends_on": [],
                "owned_paths": ["src/core/"],
                "required": True,
            },
            {
                "slice_id": "slice-b",
                "title": "Independent docs",
                "status": "active",
                "depends_on": ["slice-a"],
                "owned_paths": ["docs/guide.html"],
                "required": True,
            },
        ],
        "attempts": [
            {
                "attempt_id": "attempt-b-1",
                "slice_id": "slice-b",
                "number": 1,
                "status": "running",
                "started_at": "2026-07-11T10:05:00-04:00",
            }
        ],
        "leases": [
            {
                "lease_id": "lease-b-1",
                "slice_id": "slice-b",
                "worker_id": "worker-1",
                "status": "active",
                "epoch": 1,
                "fencing_token": 1,
                "acquired_at": "2026-07-11T10:05:00-04:00",
                "heartbeat_at": "2026-07-11T10:05:30-04:00",
                "expires_at": "2026-07-11T10:10:00-04:00",
                "owned_paths": ["docs/guide.html"],
            }
        ],
        "blockers": [],
        "stop_requests": [],
        "budget": {
            "token_limit": None,
            "tokens_used": 0,
            "time_limit_seconds": None,
            "started_at": "2026-07-11T10:00:00-04:00",
        },
        "command_receipts": [],
        "capability_invocations": [],
        "supervision_requests": [],
    }


def test_runtime_state_round_trips_to_stable_json_shape() -> None:
    payload = valid_state()

    state = CoordinatorRuntimeState.from_dict(payload)

    assert state.to_dict() == payload


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda value: value["slices"].append(deepcopy(value["slices"][0])), "duplicate slice_id"),
        (lambda value: value["slices"][1].update({"depends_on": ["missing"]}), "unknown dependency"),
        (lambda value: value["slices"][1].update({"depends_on": ["slice-b"]}), "depend on itself"),
        (lambda value: value["slices"][0].update({"depends_on": ["slice-b"]}), "dependency cycle"),
        (lambda value: value.update({"updated_at": "not-a-time"}), "updated_at must be an ISO-8601"),
        (lambda value: value["slices"][1].update({"status": "mystery"}), "unknown slice status"),
        (lambda value: value.update({"max_workers": 7}), "max_workers must be from 1 through 6"),
    ],
)
def test_runtime_state_rejects_invalid_graph_and_fields(mutate: object, message: str) -> None:
    payload = valid_state()
    mutate(payload)

    with pytest.raises(ValueError, match=message):
        CoordinatorRuntimeState.from_dict(payload)


def test_runtime_state_rejects_overlapping_active_path_ownership() -> None:
    payload = valid_state()
    payload["slices"].append(
        {
            "slice_id": "slice-c",
            "title": "Conflicting docs",
            "status": "active",
            "depends_on": ["slice-a"],
            "owned_paths": ["docs/"],
            "required": True,
        }
    )

    with pytest.raises(ValueError, match="overlapping active path ownership"):
        CoordinatorRuntimeState.from_dict(payload)


@pytest.mark.parametrize(
    ("current", "requested"),
    [
        ("proposed", "integrated"),
        ("active", "ready"),
        ("blocked", "integrated"),
        ("cancelled", "active"),
        ("integrated", "active"),
    ],
)
def test_illegal_slice_transitions_are_rejected(current: str, requested: str) -> None:
    with pytest.raises(ValueError, match="Illegal slice transition"):
        validate_slice_transition(current, requested)


@pytest.mark.parametrize(
    ("current", "requested"),
    [
        ("proposed", "ready"),
        ("ready", "active"),
        ("active", "validating"),
        ("validating", "completed"),
        ("completed", "integrated"),
        ("blocked", "ready"),
        ("active", "blocked"),
    ],
)
def test_legal_slice_transitions_are_accepted(current: str, requested: str) -> None:
    validate_slice_transition(current, requested)
