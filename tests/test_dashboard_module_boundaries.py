from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "static"


def test_dashboard_core_is_loaded_before_inline_and_feature_scripts():
    page = (STATIC / "index.html").read_text(encoding="utf-8")
    core = (STATIC / "dashboard-core.js").read_text(encoding="utf-8")

    core_tag = page.index('src="/static/dashboard-core.js"')
    inline_script = page.index("<script>", core_tag)
    engineering_tag = page.index('src="/static/engineering-assistance.js"')
    experience_tag = page.index('src="/static/experience-memory.js"')
    cloud_tag = page.index('src="/static/cloud-resources.js"')
    sandbox_tag = page.index('src="/static/sandbox.js"')

    assert core_tag < inline_script < engineering_tag < experience_tag < cloud_tag < sandbox_tag
    assert "const { el, api, appendText } = window.dashboardCore;" in page
    assert "window.dashboardCore = Object.freeze({" in core
    assert "version: 1" in core


def test_sandbox_feature_has_an_isolated_versioned_module_and_startup_refresh():
    page = (STATIC / "index.html").read_text(encoding="utf-8")
    module = (STATIC / "sandbox.js").read_text(encoding="utf-8")

    assert 'src="/static/sandbox.js"' in page
    assert "let activeSandboxTaskId" not in page
    assert "core.version!==1" in module
    assert "const {api,el}=core;" in module
    assert "async function refreshSandbox()" in module
    assert "refreshSandbox();" in module
    assert module.rstrip().endswith("})();")
    assert "innerHTML" not in module


def test_experience_memory_is_an_isolated_module_with_explicit_core_contract():
    page = (STATIC / "index.html").read_text(encoding="utf-8")
    module = (STATIC / "experience-memory.js").read_text(encoding="utf-8")

    assert 'src="/static/experience-memory.js"' in page
    assert "const experienceStatuses" not in page
    assert "async function refreshExperiences()" in module
    assert "async function refreshExperienceAutoCaptureConsent()" in module
    assert "core.version!==1" in module
    assert "const {api,el,appendText}=core;" in module
    assert "refreshExperienceAutoCaptureConsent();" in module
    assert module.rstrip().endswith("})();")
    assert "innerHTML" not in module


def test_engineering_assistance_is_loaded_as_an_independent_feature_module():
    page = (STATIC / "index.html").read_text(encoding="utf-8")
    module = (STATIC / "engineering-assistance.js").read_text(encoding="utf-8")

    assert 'src="/static/engineering-assistance.js"' in page
    assert "async function refreshSelfImprovement()" not in page
    assert "async function refreshSelfImprovement()" in module
    assert "programming-assist-form" in module
    assert "self-improvement-form" in module
    assert "core.version!==1" in module
    assert "const {api,el}=core;" in module
    assert module.rstrip().endswith("})();")
    assert "innerHTML" not in module


def test_browser_learning_is_an_isolated_feature_module():
    page = (STATIC / "index.html").read_text(encoding="utf-8")
    learning = (STATIC / "learning.js").read_text(encoding="utf-8")
    module = (STATIC / "browser-learning.js").read_text(encoding="utf-8")

    assert page.index('src="/static/learning.js"') < page.index('src="/static/browser-learning.js"')
    assert "async function refreshBrowserLearningCourses()" not in learning
    assert "let browserLearningCoursesLoadSequence" not in learning
    assert "async function refreshBrowserLearningCourses()" in module
    assert "async function loadBrowserLearningCourse(" in module
    assert "async function pollBrowserLearningCourse(" in module
    assert "core.version!==1" in module
    assert "const {api,el}=core;" in module
    assert "refreshBrowserLearningCourses();" in module
    assert module.rstrip().endswith("})();")
    assert "innerHTML" not in module

def test_storage_workflows_are_isolated_with_local_concurrency_and_refresh_ownership():
    page = (STATIC / "index.html").read_text(encoding="utf-8")
    module = (STATIC / "storage.js").read_text(encoding="utf-8")

    assert 'src="/static/storage.js"' in page
    assert "async function refreshStorageHealth()" not in page
    assert "async function refreshStorageBackups()" not in page
    assert "async function stageBackupRestore(" not in page
    assert "async function pruneStorageBackups()" not in page
    assert "async function pruneRestoreStaging()" not in page
    assert "async function createStorageBackup()" not in page
    assert "core.version !== 1" in module
    assert "let healthSequence = 0" in module
    assert "let backupsSequence = 0" in module
    assert "let mutationActive = false" in module
    assert "async function withMutation(" in module
    assert "async function refreshStorageHealth()" in module
    assert "async function refreshStorageBackups()" in module
    assert module.rstrip().endswith("})();")
    assert "innerHTML" not in module

