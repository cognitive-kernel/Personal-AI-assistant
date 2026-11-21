from subprocess import CompletedProcess

from app import autonomous_engineering


def test_run_decodes_subprocess_output_as_utf8_with_replacement(monkeypatch):
    captured = {}

    def fake_run(command, **kwargs):
        captured.update(kwargs)
        return CompletedProcess(command, 0, stdout="خروجی فارسی", stderr="")

    monkeypatch.setattr(autonomous_engineering.subprocess, "run", fake_run)

    result = autonomous_engineering._run(["example-command"])

    assert result is not None
    assert result.stdout == "خروجی فارسی"
    assert captured["encoding"] == "utf-8"
    assert captured["errors"] == "replace"
