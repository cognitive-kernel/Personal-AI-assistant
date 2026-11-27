from __future__ import annotations

import io
import json
import urllib.error

import pytest

from scripts.autonomous_engineering_admission import admission_outputs, read_settings


@pytest.mark.parametrize("paused,mode,event,task,recovery,resume,skip", [
    (True, "continuous", "workflow_dispatch", "new task", "0", "true", "true"),
    (False, "scheduled", "schedule", "", "0", "false", "true"),
    (False, "scheduled", "workflow_dispatch", "", "1", "true", "true"),
    (False, "scheduled", "workflow_dispatch", "", "0", "false", "true"),
    (False, "scheduled", "workflow_dispatch", "", "0", "true", "false"),
    (False, "scheduled", "workflow_dispatch", "manual task", "0", "false", "false"),
    (False, "continuous", "push", "", "0", "false", "false"),
])
def test_authoritative_gate(paused, mode, event, task, recovery, resume, skip):
    assert admission_outputs(paused=paused, mode=mode, event=event, manual_task=task,
                             recovery_attempt=recovery, resume_once=resume)["skip"] == skip


class FakeOpener:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.paths = []

    def open(self, request, timeout):
        assert request.get_method() == "GET"
        assert request.get_header("Authorization") == "Bearer read-only"
        self.paths.append(request.full_url)
        result = next(self.responses)
        if isinstance(result, int):
            raise urllib.error.HTTPError(request.full_url, result, "denied", {}, None)
        return io.BytesIO(json.dumps(result).encode())


def test_absent_settings_require_verified_inventory_each_time():
    inventory = {"total_count": 0, "variables": []}
    opener = FakeOpener([{"full_name": "owner/repo"}, inventory, 404, inventory, 404, inventory])
    assert read_settings("owner/repo", "read-only", opener=opener) == (False, "continuous")
    assert sum("variables?per_page=1" in path for path in opener.paths) == 3


@pytest.mark.parametrize("responses", [
    [{"full_name": "other/repo"}],
    [{"full_name": "owner/repo"}, 403],
    [{"full_name": "owner/repo"}, {"total_count": True, "variables": []}],
    [{"full_name": "owner/repo"}, {"total_count": 0, "variables": []}, 404, 403],
    [{"full_name": "owner/repo"}, {"total_count": 1, "variables": []}, {"value": "maybe"}],
])
def test_unverified_settings_fail_closed(responses):
    with pytest.raises(RuntimeError):
        read_settings("owner/repo", "read-only", opener=FakeOpener(responses))
