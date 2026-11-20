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
    assert "refreshStorageHealth();" in module
    assert "refreshStorageBackups();" in module
    assert module.rstrip().endswith("})();")
    assert "innerHTML" not in module

def test_task_dashboard_is_an_isolated_feature_with_private_refresh_and_poll_state():
    page = (STATIC / "index.html").read_text(encoding="utf-8")
    module = (STATIC / "tasks.js").read_text(encoding="utf-8")

    assert 'src="/static/tasks.js"' in page
    assert page.index('src="/static/dashboard-core.js"') < page.index('src="/static/tasks.js"')
    assert "let taskPollScheduled" not in page
    assert "let taskNextCursor" not in page
    assert "let taskRefreshSequence" not in page
    assert "function renderTasks(" not in page
    assert "async function refreshTasks(" not in page
    assert "let taskPollScheduled" in module
    assert "let taskNextCursor" in module
    assert "let taskRefreshSequence" in module
    assert "async function refreshTasks(" in module
    assert "refreshTasks();" in module
    assert module.rfind("refreshTasks();") < module.rfind("})();")
    assert "} refreshTasks();" not in module
    assert "core.version!==1" in module
    assert module.rstrip().endswith("})();")
    assert "innerHTML" not in module

def test_ui_audit_tracks_the_current_storage_and_task_module_boundaries():
    audit = (ROOT / "docs" / "UI_AUDIT_2026-10-30.md").read_text(encoding="utf-8")
    engineering_log = (ROOT / "docs" / "ENGINEERING_LOG.md").read_text(encoding="utf-8")

    assert "app/static/storage.js" in audit
    assert "app/static/tasks.js" in audit
    assert "mutation gate" in audit
    assert "concurrency regression" in engineering_log

def test_chat_and_conversation_state_are_owned_by_the_chat_module():
    page = (STATIC / "index.html").read_text(encoding="utf-8")
    module = (STATIC / "chat.js").read_text(encoding="utf-8")

    assert 'src="/static/chat.js"' in page
    assert page.index('src="/static/dashboard-core.js"') < page.index('src="/static/chat.js"')
    for symbol in (
        "currentConversationId",
        "conversationRefreshInProgress",
        "conversationLoadSequence",
        "async function refreshConversations(",
        "async function loadConversationMessages(",
    ):
        assert symbol not in page
        assert symbol in module
    assert 'el("chat-form").addEventListener' not in page
    assert 'el("chat-form").addEventListener' in module
    assert "core.version!==1" in module
    assert "refreshConversations();" in module
    assert module.rstrip().endswith("})();")
    assert "innerHTML" not in module

def test_acceptance_ledger_distinguishes_owner_reported_raw_state_from_verified_database_state():
    acceptance = (ROOT / "docs" / "ROADMAP_ACCEPTANCE.md").read_text(encoding="utf-8")

    assert "Owner clarification (2026-11-20)" in acceptance
    assert "no live database was inspected" in acceptance
    assert "migrations, fixtures, and future user data" in acceptance

def test_knowledge_source_workflows_are_isolated_from_dashboard_shell():
    page = (STATIC / "index.html").read_text(encoding="utf-8")
    module = (STATIC / "knowledge.js").read_text(encoding="utf-8")

    assert 'src="/static/knowledge.js"' in page
    for symbol in (
        "async function refreshKnowledgeSources(",
        'el("knowledge-source-ingest-form").addEventListener',
        'el("knowledge-source-search-form").addEventListener',
    ):
        assert symbol not in page
        assert symbol in module
    assert "core.version!==1" in module
    assert "knowledgeSourcesSequence" in module
    assert "requestSequence!==knowledgeSourcesSequence" in module
    assert module.rstrip().endswith("})();")
    assert "innerHTML" not in module

def test_cloud_account_consent_and_quota_workflows_are_module_owned():
    page = (STATIC / "index.html").read_text(encoding="utf-8")
    module = (STATIC / "cloud-accounts.js").read_text(encoding="utf-8")

    assert 'src="/static/cloud-accounts.js"' in page
    for symbol in (
        "let kaggleQuotaRefreshSequence",
        "async function refreshKaggleQuota(",
        "let ociLimitsRefreshSequence",
        "async function refreshOciLimits(",
        'el("kaggle-quota-enable").addEventListener',
        'el("oci-limits-enable").addEventListener',
    ):
        assert symbol not in page
        assert symbol in module
    assert "core.version!==1" in module
    assert "refreshKaggleQuota();" in module
    assert "refreshOciLimits();" in module
    assert module.rstrip().endswith("})();")
    assert "innerHTML" not in module

def test_learning_assessment_and_factual_review_are_isolated_in_one_feature_module():
    page = (STATIC / "index.html").read_text(encoding="utf-8")
    module = (STATIC / "learning-review.js").read_text(encoding="utf-8")

    assert 'src="/static/learning-review.js"' in page
    for symbol in (
        "function showLearningJob(",
        'el("learning-form").addEventListener',
        "async function refreshLearningEscalations(",
        "async function refreshFactualReviewQueue(",
        "async function refreshFactualTestQueue(",
        "async function refreshFactualTestHistory(",
    ):
        assert symbol not in page
        assert symbol in module
    assert "core.version!==1" in module
    assert module.rstrip().endswith("})();")
    assert "innerHTML" not in module

def test_settings_and_model_diagnostics_are_owned_by_versioned_settings_module():
    page = (STATIC / "index.html").read_text(encoding="utf-8")
    module = (STATIC / "settings.js").read_text(encoding="utf-8")

    assert 'src="/static/settings.js"' in page
    for symbol in (
        "async function refreshOllamaModels(",
        "const modelContextProfiles",
        "async function refreshModelContextHint(",
        "async function refreshHardwareDiagnostics(",
        'el("settings-form").addEventListener',
    ):
        assert symbol not in page
        assert symbol in module
    assert "async function refreshSettings()" in module
    assert "settingsLoadSequence" in module
    assert "requestSequence===settingsLoadSequence" in module
    assert "window.dashboardSettings=Object.freeze({version:1,refresh:refreshSettings})" in module
    assert "const settings=window.dashboardSettings" in page
    assert "core.version!==1" in module
    assert module.rstrip().endswith("})();")
    assert "innerHTML" not in module

def test_theme_and_scroll_navigation_are_separate_shell_modules():
    page = (STATIC / "index.html").read_text(encoding="utf-8")
    theme = (STATIC / "theme.js").read_text(encoding="utf-8")
    section_navigation = (STATIC / "section-navigation.js").read_text(encoding="utf-8")

    assert 'src="/static/theme.js"' in page
    assert 'src="/static/section-navigation.js"' in page
    assert "initializeDashboardTheme" not in page
    assert "initializeCurrentSectionNavigation" not in page
    assert "initializeDashboardTheme" in theme
    assert "initializeCurrentSectionNavigation" in section_navigation
    assert "localStorage" in theme
    assert "requestAnimationFrame" in section_navigation
    assert "innerHTML" not in theme + section_navigation

def test_system_health_and_degraded_storage_banner_are_module_owned():
    page = (STATIC / "index.html").read_text(encoding="utf-8")
    module = (STATIC / "system-health.js").read_text(encoding="utf-8")

    assert 'src="/static/system-health.js"' in page
    assert "async function refresh()" not in page
    assert 'el("refresh").addEventListener' not in page
    assert "async function refresh()" in module
    assert "storage-degraded-banner" in module
    assert "storage-degraded-message" in module
    assert "core.version!==1" in module
    assert "refresh();" in module
    assert module.rstrip().endswith("})();")

def test_dashboard_decomposition_inventory_matches_the_ui_audit():
    audit = (ROOT / "docs" / "UI_AUDIT_2026-10-30.md").read_text(encoding="utf-8")
    page = (STATIC / "index.html").read_text(encoding="utf-8")

    for module_name in (
        "chat.js",
        "knowledge.js",
        "storage.js",
        "tasks.js",
        "cloud-accounts.js",
        "settings.js",
        "learning-review.js",
        "system-health.js",
        "theme.js",
        "section-navigation.js",
    ):
        assert module_name in audit
        assert f'/static/{module_name}' in page

def test_learning_path_workflows_use_small_versioned_core_lesson_and_overview_modules():
    page = (STATIC / "index.html").read_text(encoding="utf-8")
    core = (STATIC / "learning-core.js").read_text(encoding="utf-8")
    lessons = (STATIC / "learning-lessons.js").read_text(encoding="utf-8")
    overview = (STATIC / "learning-overview.js").read_text(encoding="utf-8")
    paths = (STATIC / "learning.js").read_text(encoding="utf-8")

    ordered = [
        "learning-core.js",
        "learning-lessons.js",
        "learning-overview.js",
        "learning.js",
        "learning-review.js",
    ]
    positions = [page.index(f'/static/{name}') for name in ordered]
    assert positions == sorted(positions)
    assert "window.learningCore=Object.freeze(" in core
    assert "version:1" in core
    assert "window.learningLessons=Object.freeze({version:1,startLesson,stopLesson})" in lessons
    assert "window.learningOverview=Object.freeze(" in overview
    assert "window.learningCore" in paths
    assert "window.learningLessons" in paths
    assert "window.learningOverview" in paths
    assert "async function refreshDueLearningReviews()" not in paths
    assert "async function refreshLearningRecommendations()" not in paths
    assert "async function refreshLearningCapabilities()" not in paths
    assert "async function regenerateLesson(" not in paths
    assert "async function loadLearningPaths()" in paths
    assert max(len(core.splitlines()), len(lessons.splitlines()), len(overview.splitlines()), len(paths.splitlines())) < 600
    assert "innerHTML" not in core + lessons + overview + paths

