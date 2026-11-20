from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

from app.ai_learning import ui


_ROOT = Path(__file__).resolve().parents[1] / "app" / "ai_learning" / "static"


def _asset(name: str) -> str:
    return (_ROOT / name).read_text(encoding="utf-8")


def test_dashboard_is_disabled_by_default(monkeypatch):
    monkeypatch.setenv("AI_LEARNING_ENABLED", "0")
    with pytest.raises(HTTPException) as error:
        ui.learning_dashboard()
    assert error.value.status_code == 404


def test_dashboard_serves_page_when_enabled(monkeypatch):
    monkeypatch.setenv("AI_LEARNING_ENABLED", "1")
    response = ui.learning_dashboard()
    assert str(response.path).endswith("index.html")
    assert response.media_type == "text/html"


def test_static_asset_names_are_allowlisted(monkeypatch):
    monkeypatch.setenv("AI_LEARNING_ENABLED", "1")
    expected = {"app.js", "core.js", "courses.js", "runs.js", "lessons.js", "controls.js", "search.js", "style.css"}
    for name in expected:
        response = ui.learning_asset(name)
        assert Path(response.path).name == name
    with pytest.raises(HTTPException) as error:
        ui.learning_asset("../service.py")
    assert error.value.status_code == 404


def test_main_dashboard_links_to_ai_learning_module():
    dashboard = Path(__file__).resolve().parents[1] / "app" / "static" / "index.html"
    html = dashboard.read_text(encoding="utf-8")
    assert 'href="/ai-learning"' in html
    assert 'data-page="ai-learning"' not in html
    assert "یادگیری از هوش مصنوعی" in html


def test_active_dashboard_exposes_global_pause_and_recovery_controls():
    html = _asset("index.html")
    assert 'id="pause-learning"' in html
    assert 'id="resume-learning"' in html
    assert ">ادامه و دریافت درس‌های باقی‌مانده</button>" in html
    assert "بازیابی پس از قطعی برق</button>" not in html


def test_active_dashboard_exposes_progress_and_per_item_controls():
    html = _asset("index.html")
    core = _asset("core.js")
    courses = _asset("courses.js")
    lessons = _asset("lessons.js")
    controls = _asset("controls.js")
    assert 'id="course-progress"' in html
    assert "progressMeter" in core
    assert '"/topics/"+topic.id+"/pause"' in courses
    assert '"/lessons/"+l.id+"/pause"' in lessons
    assert "باقی‌مانده" in controls


def test_dashboard_has_bulk_topic_and_lesson_review_controls():
    html = _asset("index.html")
    courses = _asset("courses.js")
    lessons = _asset("lessons.js")
    assert 'id="select-all-topics"' in html
    assert 'id="approve-all-topics"' in html
    assert 'id="approve-all-lessons"' in html
    assert 'id="select-all-lessons"' in lessons
    assert 'className="lesson-check"' in lessons
    assert 'تأیید درس‌های انتخاب‌شده' in lessons
    assert 'selectedTopicIds' in courses


def test_dashboard_exposes_course_wide_lesson_selection_and_generation_controls():
    html = _asset("index.html")
    courses = _asset("courses.js")
    lessons = _asset("lessons.js")
    assert 'id="show-all-lessons"' in html
    assert 'id="select-all-course-lessons"' in html
    assert 'id="approve-selected-course-lessons"' in html
    assert 'id="learn-selected-topics"' in html
    assert '"/browser/lessons"' in courses
    assert '"/versions/"+v.id+"/unpublish"' in lessons
    assert "هشدار: متن این نسخه خالی است" in lessons
    assert "تلاش مجدد با منابع به‌روز" in lessons
    assert '"/sources/"+source.id+"/check"' in lessons


def test_topic_and_lesson_catalog_is_one_collapsible_menu():
    html = _asset("index.html")
    courses = _asset("courses.js")
    lessons = _asset("lessons.js")
    assert '<details id="catalog-menu">' in html
    assert '<details id="lesson-list-details">' in html
    assert html.count('id="topics"') == 1
    assert html.count('id="lessons"') == 1
    assert 'const card=el("details",undefined,"topic")' in courses
    assert 'if(catalog)catalog.open=true' in courses
    assert 'await lessonLoader(topic.id,inlineLessons,true)' in courses
    assert 'async function loadLessons(topicId,targetRoot,inline=false)' in lessons


def test_lesson_review_is_rendered_inline_under_its_topic():
    courses = _asset("courses.js")
    lessons = _asset("lessons.js")
    css = _asset("style.css")
    assert 'inlineLessons=el("div",undefined,"topic-inline-lessons")' in courses
    assert 'await lessonLoader(topic.id,inlineLessons,true)' in courses
    assert 'await loadLessons(parentTopicId,root,inline);if(!inline)returnToTopic(parentTopicId);' in lessons
    assert '.topic-inline-lessons[hidden]{display:none}' in css


def test_scheduled_transient_retry_is_visible_in_run_history():
    runs = _asset("runs.js")
    assert 'run.next_retry_at?"تلاش مجدد زمان‌بندی‌شده"' in runs
    assert 'run.next_retry_at+" — تلاش خودکار "+Number(run.automatic_retry_count||0)+" از ۲"' in runs


def test_dashboard_explains_global_transient_retry_cooldown():
    controls = _asset("controls.js")
    assert "state.retry_pending" in controls
    assert "state.retry_after" in controls
    assert "کارهای باقی‌مانده پس از آن ادامه پیدا می‌کنند" in controls


def test_dashboard_exposes_cross_process_worker_and_recovery_state():
    controls = _asset("controls.js")
    assert "state.worker_active" in controls
    assert "state.worker_recovery_pending" in controls
    assert "ممکن است در پردازه دیگری اجرا شود" in controls


def test_topic_checkbox_is_outside_summary_and_script_cache_is_busted():
    html = _asset("index.html")
    courses = _asset("courses.js")
    runs = _asset("runs.js")
    assert '/ai-learning-assets/app.js?v=20261120-7' in html
    assert 'head.append(el("strong",numberPath+". "+topic.title)' in courses
    assert 'card.append(head);' in courses
    assert 'const selection=el("div",undefined,"topic-select");selection.append(check);card.append(selection);' in courses
    assert 'head.append(check,el("h4","اجرای #"+run.id' in runs


def test_ai_learning_page_contains_shared_main_navigation():
    html = _asset("index.html")
    assert '<nav class="main-nav" aria-label="منوی اصلی">' in html
    assert 'href="/autonomous-engineering"' in html
    assert 'aria-current="page">یادگیری از هوش مصنوعی</a>' in html


def test_ai_learning_page_has_one_loopback_protected_route_owner():
    from app.main import app

    routes = [
        route for route in app.routes
        if getattr(route, "path", None) == "/ai-learning"
    ]
    assert len(routes) == 1
    assert routes[0].name == "learning_dashboard"
    assert routes[0].dependant.dependencies


def test_ai_learning_modules_have_versioned_contracts_and_bounded_ownership():
    contracts = {
        "core.js": ("CORE_CONTRACT_VERSION", "createCore"),
        "courses.js": ("COURSES_CONTRACT_VERSION", "createCoursesModule"),
        "runs.js": ("RUNS_CONTRACT_VERSION", "createRunsModule"),
        "lessons.js": ("LESSONS_CONTRACT_VERSION", "createLessonsModule"),
        "controls.js": ("CONTROLS_CONTRACT_VERSION", "createControlsModule"),
        "search.js": ("SEARCH_CONTRACT_VERSION", "createSearchModule"),
    }
    for filename, (contract, factory) in contracts.items():
        source = _asset(filename)
        assert f"export const {contract}=1;" in source
        assert f"export function {factory}(" in source
        assert not any(line.startswith("import ") for line in source.splitlines())
        assert (_ROOT / filename).stat().st_size < 24_000

    entry = _asset("app.js")
    assert (_ROOT / "app.js").stat().st_size < 7_000
    assert "Promise.allSettled" in entry
    assert "function importWithTimeout(path,timeoutMs=5000)" in entry
    assert "module load timeout:" in entry
    for helper in ("async function api(", "function say(", "function el(", "function data(", "function progressMeter(", "async function act("):
        assert helper in entry
    courses = _asset("courses.js")
    search = _asset("search.js")
    assert "loadCourses(created.id)" in courses
    assert "Promise.resolve().then(()=>runHistoryLoader(requestedCourseId)).catch" in courses
    assert "searchRequestSequence" in search
    assert 'import("./core.js?v="+MODULE_VERSION)' in entry
    assert 'import("./courses.js?v="+MODULE_VERSION)' in entry
    assert "function createFallbackCore()" in entry
    assert "function loadFeature(" in entry
    assert "courses.setLessonLoader(lessons?.loadLessons" in entry
    assert "courses.setRunHistoryLoader(runs?.loadRuns" in entry
    assert "function loadCourse(" not in entry
    assert "function loadLessons(" not in entry
    assert "function loadRuns(" not in entry
