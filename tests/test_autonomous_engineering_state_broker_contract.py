from __future__ import annotations

import pytest

from app.autonomous_engineering_state_broker_contract import validate_task_request


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/v1/tasks/pending"),
        ("GET", "/v1/tasks/archived"),
    ],
)
def test_pending_task_list_contract_has_no_request_body(method, path):
    assert validate_task_request(method, path) is None
    with pytest.raises(ValueError):
        validate_task_request(method, path, {"path": "arbitrary"})


@pytest.mark.parametrize("path", ["/v1/tasks/archive", "/v1/tasks/restore"])
def test_pending_task_mutations_require_opaque_id_and_revision(path):
    task_id = "a" * 64
    revision = "b" * 64
    assert validate_task_request("POST", path, {
        "task_id": task_id,
        "revision": revision,
    }) == {"task_id": task_id, "revision": revision}


@pytest.mark.parametrize(
    "body",
    [
        None,
        {},
        {"task_id": "a" * 64},
        {"task_id": "a" * 64, "revision": "b" * 64, "path": "x"},
        {"task_id": "../state", "revision": "b" * 64},
        {"task_id": "a" * 64, "revision": "../state"},
    ],
)
def test_pending_task_mutations_reject_incomplete_or_path_bearing_bodies(body):
    with pytest.raises(ValueError):
        validate_task_request("POST", "/v1/tasks/archive", body)


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("DELETE", "/v1/tasks/pending"),
        ("GET", "/v1/tasks/pending?path=/etc/passwd"),
        ("POST", "/v1/tasks/purge"),
        ("POST", "/v1/files/delete"),
        ("POST", "/v1/tasks/archive/../../state"),
    ],
)
def test_pending_task_contract_rejects_unknown_or_path_based_operations(method, path):
    with pytest.raises(ValueError):
        validate_task_request(method, path, None)
