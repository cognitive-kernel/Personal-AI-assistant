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
    assert "یادگیری از هوش مصنوعی" in html
