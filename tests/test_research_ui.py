from pathlib import Path


def test_chat_ui_uses_persistent_research_queue_and_polls_task_result():
    page = (Path(__file__).parents[1] / "app" / "static" / "index.html").read_text(
        encoding="utf-8"
    )
    assert '"/api/research/tasks"' in page
    assert '"/api/tasks/"+taskId' in page
    assert 'current.result&&current.result.reply' in page
    assert 'در حال صف‌بندی پژوهش' in page
    assert 'در حال پاسخ‌گویی' not in page
