from __future__ import annotations

import pytest

from scripts.ollama_temporary_context import (
    OllamaTemporaryContextError,
    cleanup_temporary_context_model,
    temporary_context_model_name,
)


class FakeOllama:
    def __init__(
        self,
        names,
        *,
        loaded=(),
        modelfiles=None,
        keep_loaded_after_unload=False,
        keep_after_delete=False,
        remove_after_delete=(),
    ):
        self.names = list(names)
        self.loaded = list(loaded)
        self.modelfiles = dict(modelfiles or {})
        self.keep_loaded_after_unload = keep_loaded_after_unload
        self.keep_after_delete = keep_after_delete
        self.remove_after_delete = tuple(remove_after_delete)
        self.calls = []

    def request(self, method, path, body=None, timeout=10.0):
        self.calls.append((method, path, body, timeout))
        if method == "GET" and path == "/api/tags":
            return {"models": [{"name": name} for name in self.names]}
        if method == "GET" and path == "/api/ps":
            return {"models": [{"name": name} for name in self.loaded]}
        if method == "POST" and path == "/api/show":
            name = body["model"]
            if name not in self.names:
                raise OllamaTemporaryContextError("unknown model")
            return {"modelfile": self.modelfiles.get(name, "")}
        if method == "POST" and path == "/api/generate":
            name = body["model"]
            assert body.get("keep_alive") == 0
            if not self.keep_loaded_after_unload:
                self.loaded = [item for item in self.loaded if item.casefold() != name.casefold()]
            return {"done": True}
        if method == "DELETE" and path == "/api/delete":
            name = body["model"]
            if not self.keep_after_delete:
                self.names = [item for item in self.names if item.casefold() != name.casefold()]
                self.names = [
                    item for item in self.names
                    if item.casefold() not in {value.casefold() for value in self.remove_after_delete}
                ]
            return {"status": "success"}
        raise AssertionError(f"Unexpected Ollama request: {method} {path}")


def make_model_name():
    return temporary_context_model_name(
        "qwen2.5:7b",
        2048,
        run_id="123456",
        run_attempt="2",
        nonce="a1b2c3d4e5f6",
    )


def make_api(model_name, **kwargs):
    return FakeOllama(
        ["qwen2.5:7b", "unrelated:1b", model_name],
        loaded=[model_name],
        modelfiles={
            model_name: "FROM /blob/qwen\nPARAMETER num_ctx 2048\n",
        },
        **kwargs,
    )


def test_temporary_model_name_is_scoped_to_context_run_attempt_and_nonce():
    model = make_model_name()

    assert model == "qwen2.5:7b-codex-ctx2048-paa-123456-2-a1b2c3d4e5f6"


def test_cleanup_unloads_and_deletes_only_the_owned_temporary_variant():
    model = make_model_name()
    api = make_api(model)

    result = cleanup_temporary_context_model(
        model,
        run_id="123456",
        run_attempt="2",
        expected_context=2048,
        request_json=api.request,
        sleep=lambda _seconds: None,
    )

    assert result["removed"] is True
    assert result["model"] == model
    assert "qwen2.5:7b" in api.names
    assert "unrelated:1b" in api.names
    assert model not in api.names
    assert model not in api.loaded
    calls = [(method, path) for method, path, _body, _timeout in api.calls]
    assert calls.index(("POST", "/api/generate")) < calls.index(("DELETE", "/api/delete"))


def test_cleanup_is_idempotent_when_temporary_model_is_already_absent():
    model = make_model_name()
    api = FakeOllama(["qwen2.5:7b"])

    result = cleanup_temporary_context_model(
        model,
        run_id="123456",
        run_attempt="2",
        expected_context=2048,
        request_json=api.request,
    )

    assert result["removed"] is False
    assert api.names == ["qwen2.5:7b"]
    assert not [call for call in api.calls if call[0] == "DELETE"]


@pytest.mark.parametrize(
    ("run_id", "run_attempt", "expected_context"),
    [
        ("999999", "2", 2048),
        ("123456", "1", 2048),
        ("123456", "2", 4096),
    ],
)
def test_cleanup_refuses_wrong_run_attempt_or_context_without_deleting(
    run_id, run_attempt, expected_context
):
    model = make_model_name()
    api = make_api(model)

    with pytest.raises(OllamaTemporaryContextError):
        cleanup_temporary_context_model(
            model,
            run_id=run_id,
            run_attempt=run_attempt,
            expected_context=expected_context,
            request_json=api.request,
        )

    assert model in api.names
    assert not [call for call in api.calls if call[0] == "DELETE"]


def test_cleanup_preserves_model_when_unload_cannot_be_verified():
    model = make_model_name()
    api = make_api(model, keep_loaded_after_unload=True)

    with pytest.raises(OllamaTemporaryContextError, match="still reports the temporary model as loaded"):
        cleanup_temporary_context_model(
            model,
            run_id="123456",
            run_attempt="2",
            expected_context=2048,
            request_json=api.request,
            sleep=lambda _seconds: None,
            verify_attempts=2,
        )

    assert model in api.names
    assert model in api.loaded
    assert not [call for call in api.calls if call[0] == "DELETE"]


def test_cleanup_preserves_model_when_ollama_does_not_confirm_deletion():
    model = make_model_name()
    api = make_api(model, keep_after_delete=True)

    with pytest.raises(OllamaTemporaryContextError, match="still lists"):
        cleanup_temporary_context_model(
            model,
            run_id="123456",
            run_attempt="2",
            expected_context=2048,
            request_json=api.request,
            sleep=lambda _seconds: None,
            verify_attempts=2,
        )

    assert model in api.names


@pytest.mark.parametrize(
    "model",
    [
        "qwen2.5:7b-codex-ctx2048",
        "qwen2.5:7b-codex-ctx2048-paa-123456-2-not-a-nonce",
        "qwen2.5:7b-codex-ctx2048-paa-123456-2-a1b2c3d4e5f6-extra",
    ],
)
def test_cleanup_refuses_legacy_or_unowned_model_names(model):
    api = make_api(model)

    with pytest.raises(OllamaTemporaryContextError):
        cleanup_temporary_context_model(
            model,
            run_id="123456",
            run_attempt="2",
            expected_context=2048,
            request_json=api.request,
        )

    assert model in api.names
    assert not [call for call in api.calls if call[0] == "DELETE"]


def test_cleanup_reports_if_base_tag_disappears_during_temporary_deletion():
    model = make_model_name()
    api = make_api(model, remove_after_delete=["qwen2.5:7b"])

    with pytest.raises(OllamaTemporaryContextError, match="base-model tag disappeared during cleanup"):
        cleanup_temporary_context_model(
            model,
            run_id="123456",
            run_attempt="2",
            expected_context=2048,
            request_json=api.request,
            sleep=lambda _seconds: None,
        )

    assert model not in api.names
    assert "qwen2.5:7b" not in api.names
    assert "unrelated:1b" in api.names
