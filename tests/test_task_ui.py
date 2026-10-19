from pathlib import Path


def test_task_dashboard_exposes_cursor_pagination_controls():
    source = (Path(__file__).parents[1] / "app" / "static" / "index.html").read_text(
        encoding="utf-8"
    )
    assert "taskPageCursors" in source
    assert "taskNextCursor" in source
    assert 'textContent="صفحهٔ قبل"' in source
    assert 'textContent="صفحهٔ بعد"' in source
    assert 'params.set("cursor",cursor)' in source
