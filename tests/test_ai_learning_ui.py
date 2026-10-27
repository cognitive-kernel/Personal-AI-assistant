from __future__ import annotations
import pytest
from fastapi import HTTPException
from app.ai_learning import ui

def test_dashboard_is_disabled_by_default(monkeypatch):
    monkeypatch.setenv("AI_LEARNING_ENABLED","0")
    with pytest.raises(HTTPException) as error:
        ui.learning_dashboard()
    assert error.value.status_code==404

def test_dashboard_serves_page_when_enabled(monkeypatch):
    monkeypatch.setenv("AI_LEARNING_ENABLED","1")
    response=ui.learning_dashboard()
    assert str(response.path).endswith("index.html")
    assert response.media_type=="text/html"

def test_static_asset_names_are_allowlisted(monkeypatch):
    monkeypatch.setenv("AI_LEARNING_ENABLED","1")
    with pytest.raises(HTTPException) as error:
        ui.learning_asset("../service.py")
    assert error.value.status_code==404
 
def test_main_dashboard_links_to_ai_learning_module():
    from pathlib import Path

    dashboard = Path(__file__).resolve().parents[1] / "app" / "static" / "index.html"
    html = dashboard.read_text(encoding="utf-8")
    assert 'href="/ai-learning"' in html
    assert 'data-page="ai-learning"' not in html  # page-navigation rewrites data-page links
    assert "یادگیری از هوش مصنوعی" in html


def test_active_dashboard_exposes_global_pause_and_recovery_controls():
    from pathlib import Path

    page = Path(__file__).resolve().parents[1] / "app" / "ai_learning" / "static" / "index.html"
    html = page.read_text(encoding="utf-8")
    assert 'id="pause-learning"' in html
    assert 'id="resume-learning"' in html
    assert ">ادامه و دریافت درس‌های باقی‌مانده</button>" in html
    assert "بازیابی پس از قطعی برق</button>" not in html


def test_active_dashboard_exposes_progress_and_per_item_controls():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "app" / "ai_learning" / "static"
    html = (root / "index.html").read_text(encoding="utf-8")
    script = (root / "app.js").read_text(encoding="utf-8")
    assert 'id="course-progress"' in html
    assert "progressMeter" in script
    assert '"/topics/"+topic.id+"/pause"' in script
    assert '"/lessons/"+l.id+"/pause"' in script
    assert "باقی‌مانده" in script


def test_dashboard_has_bulk_topic_and_lesson_review_controls():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "app" / "ai_learning" / "static"
    html = (root / "index.html").read_text(encoding="utf-8")
    script = (root / "app.js").read_text(encoding="utf-8")
    assert 'id="select-all-topics"' in html
    assert 'id="approve-all-topics"' in html
    assert 'id="approve-all-lessons"' in html
    assert 'id="select-all-lessons"' in script
    assert 'className="lesson-check"' in script
    assert 'تأیید درس‌های انتخاب‌شده' in script

def test_dashboard_exposes_course_wide_lesson_selection_and_generation_controls():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "app" / "ai_learning" / "static"
    html = (root / "index.html").read_text(encoding="utf-8")
    script = (root / "app.js").read_text(encoding="utf-8")
    assert 'id="show-all-lessons"' in html
    assert 'id="select-all-course-lessons"' in html
    assert 'id="approve-selected-course-lessons"' in html
    assert 'id="learn-selected-topics"' in html
    assert '"/browser/lessons"' in script
    assert '"/versions/"+v.id+"/unpublish"' in script
    assert "هشدار: متن این نسخه خالی است" in script
    assert "تلاش مجدد با منابع به‌روز" in script
    assert '"/sources/"+source.id+"/check"' in script


def test_topic_and_lesson_catalog_is_one_collapsible_menu():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "app" / "ai_learning" / "static"
    html = (root / "index.html").read_text(encoding="utf-8")
    script = (root / "app.js").read_text(encoding="utf-8")
    assert '<details id="catalog-menu">' in html
    assert '<details id="lesson-list-details">' in html
    assert html.count('id="topics"') == 1
    assert html.count('id="lessons"') == 1
    assert 'const card=el("details",undefined,"topic")' in script
    assert 'if(catalog)catalog.open=true' in script
    assert 'if(lessonDetails)lessonDetails.open=true' in script
