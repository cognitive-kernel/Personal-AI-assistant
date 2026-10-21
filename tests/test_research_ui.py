from pathlib import Path


def test_chat_ui_uses_persistent_research_queue_and_polls_task_result():
    app_root = Path(__file__).parents[1] / "app"
    shell = (app_root / "static" / "index.html").read_text(encoding="utf-8")
    chat_section = (app_root / "dashboard_sections" / "chat.html").read_text(encoding="utf-8")
    tasks_section = (app_root / "dashboard_sections" / "tasks.html").read_text(encoding="utf-8")
    page = shell + "\n" + chat_section + "\n" + tasks_section
    assert 'id="chat-section"' in chat_section
    assert 'id="task-manager-section"' in tasks_section
    assert 'id="load-more-tasks"' in tasks_section
    assert '"/api/research/tasks"' in page
    assert '"/api/tasks/"+taskId' in page
    assert 'current.result&&current.result.reply' in page
    assert 'در حال صف‌بندی پژوهش' in page
    assert 'متن پیام تا پایان کار در پایگاه وظایف محلی نگهداری می‌شود' in page
    assert 'id="task-manager-section"' in page
    assert 'new URLSearchParams({limit:"50"})' in page
    assert 'query.set("cursor",taskNextCursor)' in page
    assert 'id="load-more-tasks"' in page
    assert '"/api/tasks/"+task.id+"/pause"' in page
    assert '"/api/tasks/"+task.id+"/resume"' in page
    assert '"/api/tasks/"+task.id+"/cancel"' in page
    assert 'در حال پاسخ‌گویی' not in page
