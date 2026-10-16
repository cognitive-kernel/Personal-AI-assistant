from pathlib import Path


def test_local_ui_exposes_sandbox_consent_status_and_bounded_execution():
    html_path = Path(__file__).parents[1] / "app" / "static" / "index.html"
    html = html_path.read_text(encoding="utf-8")

    assert 'id="sandbox-section"' in html
    assert 'id="sandbox-consent-confirm"' in html
    assert 'id="sandbox-consent-grant"' in html
    assert 'id="sandbox-consent-revoke"' in html
    assert 'id="sandbox-cancel" type="button" disabled' in html
    assert 'api("/api/tasks/"+taskId+"/cancel"' in html
    assert 'id="sandbox-run-form"' in html
    assert 'id="sandbox-run" type="submit" disabled' in html
    assert 'api("/api/sandbox/consent")' in html
    assert 'api("/api/sandbox/run"' in html
    assert 'api("/api/tasks/"+task.id)' in html
    assert 'max="120"' in html
    assert 'max="65536"' in html


def test_local_ui_never_enables_sandbox_when_status_lookup_fails():
    html_path = Path(__file__).parents[1] / "app" / "static" / "index.html"
    html = html_path.read_text(encoding="utf-8")

    failure_branch = html.split('async function refreshSandbox()', 1)[1].split(
        'el("sandbox-refresh")', 1
    )[0]
    assert 'runButton.disabled=true;' in failure_branch
    assert 'status.textContent="وضعیت sandbox قابل دریافت نیست؛ اجرا غیرفعال می‌ماند. "' in failure_branch
