import pytest
from fastapi import HTTPException

from app import db
from app.module_settings import (
    MODULE_SETTING_KEYS,
    ensure_module_enabled,
    is_module_enabled,
    require_module_enabled,
)


@pytest.fixture
def isolated_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()
    return db


def test_every_registered_module_is_enabled_on_fresh_install(isolated_settings):
    settings = isolated_settings.get_settings()
    assert set(MODULE_SETTING_KEYS.values()).issubset(settings)
    assert all(is_module_enabled(module, settings) for module in MODULE_SETTING_KEYS)


@pytest.mark.parametrize("module,key", list(MODULE_SETTING_KEYS.items()))
def test_each_module_can_be_disabled_independently(isolated_settings, module, key):
    isolated_settings.update_settings({key: False})
    settings = isolated_settings.get_settings()
    assert is_module_enabled(module, settings) is False
    for other_module, other_key in MODULE_SETTING_KEYS.items():
        if other_module != module:
            assert settings[other_key] is True


def test_disabled_module_dependency_returns_conflict(isolated_settings):
    isolated_settings.update_settings({"chat_enabled": False})
    dependency = require_module_enabled("chat")

    with pytest.raises(HTTPException) as exc:
        dependency()

    assert exc.value.status_code == 409
    assert "chat" in str(exc.value.detail)


def test_unknown_module_is_rejected_instead_of_silently_enabled(isolated_settings):
    with pytest.raises(KeyError, match="Unknown application module"):
        ensure_module_enabled("unknown-module")
