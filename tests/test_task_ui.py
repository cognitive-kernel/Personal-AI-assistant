from pathlib import Path


def test_task_dashboard_exposes_cursor_pagination_controls():
    source = (Path(__file__).parents[1] / "app" / "static" / "index.html").read_text(
        encoding="utf-8"
    )
    assert "taskNextCursor" in source
    assert "load-more-tasks" in source
    assert 'query.set("cursor",taskNextCursor)' in source


def test_task_dashboard_exposes_status_history_action():
    source = (Path(__file__).parents[1] / "app" / "static" / "index.html").read_text(
        encoding="utf-8"
    )
    assert 'textContent="سابقهٔ وضعیت"' in source
    assert '/events?limit=100' in source
    assert 'history.textContent=' in source
