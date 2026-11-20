from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_chat_feedback_controls_are_accessible_and_persisted():
    page = (ROOT / "app" / "static" / "index.html").read_text(encoding="utf-8")
    stylesheet = (ROOT / "app" / "static" / "dashboard.css").read_text(encoding="utf-8")

    assert 'className = "chat-feedback"' in page
    assert 'button.setAttribute("aria-pressed"' in page
    assert 'button.setAttribute("aria-label", label)' in page
    assert '"/messages/" + encodeURIComponent(message.id) + "/feedback"' in page
    assert 'JSON.stringify({rating})' in page
    assert "این امتیاز به‌تنهایی صحت دانش را تأیید نمی‌کند" in page
    assert ".chat-feedback button[aria-pressed=\"true\"]" in stylesheet
