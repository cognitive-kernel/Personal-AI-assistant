from pathlib import Path


STATIC = Path(__file__).resolve().parents[1] / "app" / "static"


def test_chat_ui_uses_persistent_research_queue_and_polls_task_result():
    page = (STATIC / "index.html").read_text(encoding="utf-8")
    chat = (STATIC / "chat.js").read_text(encoding="utf-8")
    tasks = (STATIC / "tasks.js").read_text(encoding="utf-8")

    assert '"/api/research/tasks"' in chat
    assert '"/api/tasks/"+taskId' in chat
    assert 'await refreshConversations({selectId:currentConversationId,createIfEmpty:false});' in chat
    assert 'el("chat-output").textContent="پاسخ ذخیره شد."' in chat
    assert 'در حال ثبت پیام و صف‌بندی پاسخ…' in chat
    assert 'id="task-manager-section"' in page
    assert 'new URLSearchParams({limit:"50"})' in tasks
    assert 'query.set("cursor",taskNextCursor)' in tasks
    assert 'id="load-more-tasks"' in page
    assert '"/api/tasks/"+task.id+"/pause"' in tasks
    assert '"/api/tasks/"+task.id+"/resume"' in tasks
    assert '"/api/tasks/"+task.id+"/cancel"' in tasks
    assert 'در حال پاسخ‌گویی' not in page + chat + tasks


def test_task_manager_ui_exposes_confirmed_deletion_for_inactive_tasks():
    page = (STATIC / "index.html").read_text(encoding="utf-8")
    tasks = (STATIC / "tasks.js").read_text(encoding="utf-8")
    assert 'id="task-manager-section"' in page
    assert 'textContent="حذف وظیفه"' in tasks
    assert 'api("/api/tasks/"+encodeURIComponent(task.id),{method:"DELETE"})' in tasks
    assert '["queued","paused","cancelled","succeeded","failed"].includes(task.status)' in tasks
    assert 'window.confirm("وظیفهٔ #"+task.id' in tasks
