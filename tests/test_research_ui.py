from pathlib import Path


def test_chat_ui_uses_persistent_research_queue_and_polls_task_result():
    page = (Path(__file__).parents[1] / "app" / "static" / "index.html").read_text(
        encoding="utf-8"
    )
    assert '"/api/research/tasks"' in page
    assert '"/api/tasks/"+taskId' in page
    assert 'await refreshConversations({selectId:currentConversationId,createIfEmpty:false});' in page
    assert 'el("chat-output").textContent="پاسخ ذخیره شد."' in page
    assert 'در حال ثبت پیام و صف‌بندی پاسخ…' in page
    assert 'در حال ثبت پیام و صف‌بندی پاسخ…' in page
    assert 'id="task-manager-section"' in page
    assert 'new URLSearchParams({limit:"50"})' in page
    assert 'query.set("cursor",taskNextCursor)' in page
    assert 'id="load-more-tasks"' in page
    assert '"/api/tasks/"+task.id+"/pause"' in page
    assert '"/api/tasks/"+task.id+"/resume"' in page
    assert '"/api/tasks/"+task.id+"/cancel"' in page
    assert 'در حال پاسخ‌گویی' not in page
