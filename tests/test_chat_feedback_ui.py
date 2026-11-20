from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_chat_feedback_controls_are_accessible_and_persisted():
    module = (ROOT / "app" / "static" / "chat.js").read_text(encoding="utf-8")
    stylesheet = (ROOT / "app" / "static" / "dashboard.css").read_text(encoding="utf-8")

    assert 'className = "chat-feedback"' in module
    assert 'button.setAttribute("aria-pressed"' in module
    assert 'button.setAttribute("aria-label", label)' in module
    assert '"/messages/" + encodeURIComponent(message.id) + "/feedback"' in module
    assert 'JSON.stringify({rating})' in module
    assert "این امتیاز به‌تنهایی صحت دانش را تأیید نمی‌کند" in module
    assert ".chat-feedback button[aria-pressed=\"true\"]" in stylesheet
