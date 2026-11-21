from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "static"


def test_dashboard_core_and_learning_candidates_are_external_feature_scripts():
    page = (STATIC / "index.html").read_text(encoding="utf-8")
    core = (STATIC / "dashboard-core.js").read_text(encoding="utf-8")

    core_tag = page.index('src="/static/dashboard-core.js"')
    candidates_tag = page.index('src="/static/learning-candidates.js"')
    engineering_tag = page.index('src="/static/engineering-assistance.js"')
    experience_tag = page.index('src="/static/experience-memory.js"')
    cloud_tag = page.index('src="/static/cloud-resources.js"')
    sandbox_tag = page.index('src="/static/sandbox.js"')

    assert core_tag < candidates_tag < engineering_tag < experience_tag < cloud_tag < sandbox_tag
    assert "<script>" not in page
    assert "refreshLearningCandidates" not in page
    assert "Object.defineProperty(window, \"dashboardCore\"" in core
    assert "configurable: false" in core
    assert "writable: false" in core
    assert "version: 1" in core


def test_dashboard_feature_modules_fallback_when_the_shared_core_asset_fails():
    modules = (
        "browser-learning.js", "chat.js", "cloud-accounts.js", "cloud-resources.js",
        "engineering-assistance.js", "experience-memory.js", "knowledge.js",
        "learning-core.js", "learning-review.js", "sandbox.js", "storage.js",
        "system-health.js", "tasks.js", "settings.js", "learning-candidates.js",
    )
    for name in modules:
        source = (STATIC / name).read_text(encoding="utf-8")
        assert "createLocalDashboardCore" in source, name
        assert "window.dashboardCore" in source, name


def test_learning_submodules_degrade_independently_when_learning_core_assets_fail():
    lessons = (STATIC / "learning-lessons.js").read_text(encoding="utf-8")
    overview = (STATIC / "learning-overview.js").read_text(encoding="utf-8")
    orchestrator = (STATIC / "learning.js").read_text(encoding="utf-8")

    assert "createLocalLearningCore" in lessons
    assert "createLocalLearningCore" in overview
    assert "window.learningCore && window.learningCore.version===1" in orchestrator
    assert "lessons?.startLesson" in orchestrator
    assert "overview?.refreshDueReviews" in orchestrator


def test_autonomous_engineering_submodules_have_independent_core_fallbacks():
    for name in (
        "autonomous-engineering-controls.js",
        "autonomous-engineering-progress.js",
        "autonomous-engineering-runs.js",
        "autonomous-engineering-status.js",
        "autonomous-engineering.js",
    ):
        source = (STATIC / name).read_text(encoding="utf-8")
        assert "createLocalAutonomousEngineeringCore" in source, name


def test_sandbox_feature_has_an_isolated_versioned_module_and_startup_refresh():
    page = (STATIC / "index.html").read_text(encoding="utf-8")
    module = (STATIC / "sandbox.js").read_text(encoding="utf-8")

    assert 'src="/static/sandbox.js"' in page
    assert "let activeSandboxTaskId" not in page
    assert "window.dashboardCore&&window.dashboardCore.version===1?window.dashboardCore:createLocalDashboardCore()" in module
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
    assert "window.dashboardCore&&window.dashboardCore.version===1?window.dashboardCore:createLocalDashboardCore()" in module
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
    assert "window.dashboardSettings" not in page
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

def test_learning_architecture_documentation_lists_the_split_modules():
    docs = (ROOT / "docs" / "LEARNING.md").read_text(encoding="utf-8")
    audit = (ROOT / "docs" / "UI_AUDIT_2026-10-30.md").read_text(encoding="utf-8")

    for name in ("learning-core.js", "learning-lessons.js", "learning-overview.js", "learning-review.js"):
        assert name in docs
        assert name in audit

def test_remaining_large_ui_modules_are_not_misreported_as_complete():
    status = (ROOT / "docs" / "PROJECT_STATUS.md").read_text(encoding="utf-8")
    engineering_log = (ROOT / "docs" / "ENGINEERING_LOG.md").read_text(encoding="utf-8")

    assert not (STATIC / "ai-learning.js").exists()
    assert "app/ai_learning/static/app.js" in status
    assert "learning.js" in status
    assert "learning.js" in engineering_log
    assert "ai-learning.js" in engineering_log  # Historical root-cause record, not an active module.
    assert "prevents declaring the no-god-module objective complete" in status
    orchestrator = (STATIC / "autonomous-engineering.js").read_text(encoding="utf-8")
    fallback_start = orchestrator.index("function createLocalAutonomousEngineeringCore()")
    fallback_end = orchestrator.index("\n}", fallback_start) + 2
    orchestrator_logic = orchestrator[:fallback_start] + orchestrator[fallback_end:]
    assert len(orchestrator_logic.splitlines()) <= 40

def test_dashboard_module_architecture_decision_records_contract_and_merge_gates():
    adr = (ROOT / "docs" / "adr" / "0001-dashboard-feature-module-contracts.md").read_text(encoding="utf-8")

    assert "Status:** Proposed" in adr
    assert "dashboardCore v1" in adr
    assert "learningCore v1" in adr
    assert "Stale read responses" in adr
    assert "Do not merge" in adr
    assert "No live database was accessed or modified" in adr



def test_system_health_refresh_is_single_and_does_not_depend_on_settings():
    module = (STATIC / "system-health.js").read_text(encoding="utf-8")

    assert 'src="/static/system-health.js"' in (STATIC / "index.html").read_text(encoding="utf-8")
    assert module.count('el("refresh").addEventListener("click"') == 1
    assert module.count(" refresh();") == 1
    assert "dashboardSettings" not in module



def test_learning_review_panels_ignore_stale_out_of_order_responses():
    module = (STATIC / "learning-review.js").read_text(encoding="utf-8")
    panels = (
        ("refreshLearningEscalations", "learningEscalationsRequestSequence"),
        ("refreshFactualReviewQueue", "factualReviewQueueRequestSequence"),
        ("refreshFactualTestQueue", "factualTestQueueRequestSequence"),
        ("refreshFactualTestHistory", "factualTestHistoryRequestSequence"),
    )
    for function_name, sequence in panels:
        assert f"let {sequence}=0;" in module
        start = module.index(f"async function {function_name}()")
        end = module.find("\nasync function ", start + 1)
        block = module[start:end if end >= 0 else len(module)]
        guard = f"if(requestSequence!=={sequence})return;"
        assert f"const requestSequence=++{sequence};" in block
        assert guard in block
        assert block.index(guard) < block.index("container.replaceChildren();")
        assert f"if(requestSequence==={sequence})" in block


def test_learning_capability_refresh_ignores_stale_out_of_order_responses():
    module = (STATIC / "learning-overview.js").read_text(encoding="utf-8")

    assert "let learningCapabilitiesRequestSequence = 0;" in module
    assert "const requestSequence = ++learningCapabilitiesRequestSequence;" in module
    assert "if (requestSequence !== learningCapabilitiesRequestSequence) return;" in module
    assert "if (requestSequence === learningCapabilitiesRequestSequence)" in module


def test_learning_capability_guard_runs_before_rendering_latest_response():
    module = (STATIC / "learning-overview.js").read_text(encoding="utf-8")
    start = module.index("async function refreshLearningCapabilities()")
    end = module.find("\n}", start)
    block = module[start:end if end >= 0 else len(module)]

    await_result = 'await learningApi("/api/learning/capabilities?limit=200")'
    guard = "if (requestSequence !== learningCapabilitiesRequestSequence) return;"
    assert await_result in block
    assert guard in block
    assert block.index(await_result) < block.index(guard) < block.index("const items = result.capabilities || [];")
    assert "if (requestSequence === learningCapabilitiesRequestSequence)" in block



def test_settings_refresh_paths_discard_stale_model_and_hardware_responses():
    module = (STATIC / "settings.js").read_text(encoding="utf-8")

    assert "let ollamaModelsRequestSequence=0;" in module
    assert "const modelContextHintRequestSequences=new Map();" in module
    assert "let hardwareDiagnosticsRequestSequence=0;" in module

    catalog_start = module.index("async function refreshOllamaModels()")
    catalog_end = module.index("const modelContextHintRequestSequences", catalog_start)
    catalog = module[catalog_start:catalog_end]
    assert "const requestSequence=++ollamaModelsRequestSequence;" in catalog
    assert "const isCurrent=()=>requestSequence===ollamaModelsRequestSequence" in catalog
    assert 'const result=await api("/api/models");\n   if(!isCurrent()) return;' in catalog

    context_start = module.index("async function refreshModelContextHint(")
    context_end = module.index("let hardwareDiagnosticsRequestSequence", context_start)
    context = module[context_start:context_end]
    assert "modelContextHintRequestSequences.set(purpose,requestSequence);" in context
    assert "if(!isCurrent()) return;" in context
    assert "if(isCurrent()) {" in context

    hardware_start = module.index("async function refreshHardwareDiagnostics(")
    hardware_end = module.index("for(const profile of modelContextProfiles)", hardware_start)
    hardware = module[hardware_start:hardware_end]
    assert "const requestSequence=++hardwareDiagnosticsRequestSequence;" in hardware
    assert "if(!isCurrent()) return;" in hardware
    assert "if(isCurrent()) output.textContent=" in hardware



def test_autonomous_engineering_console_uses_independent_versioned_feature_modules():
    page = (STATIC / "autonomous-engineering.html").read_text(encoding="utf-8")
    script_paths = (
        "/static/autonomous-engineering-core.js",
        "/static/autonomous-engineering-runs.js",
        "/static/autonomous-engineering-status.js",
        "/static/autonomous-engineering-controls.js",
        "/static/autonomous-engineering-progress.js",
        "/static/autonomous-engineering.js",
    )
    positions = [page.index(f'src="{path}"') for path in script_paths]
    assert positions == sorted(positions)

    core = (STATIC / "autonomous-engineering-core.js").read_text(encoding="utf-8")
    runs = (STATIC / "autonomous-engineering-runs.js").read_text(encoding="utf-8")
    status = (STATIC / "autonomous-engineering-status.js").read_text(encoding="utf-8")
    controls = (STATIC / "autonomous-engineering-controls.js").read_text(encoding="utf-8")
    progress = (STATIC / "autonomous-engineering-progress.js").read_text(encoding="utf-8")
    orchestrator = (STATIC / "autonomous-engineering.js").read_text(encoding="utf-8")

    assert "window.autonomousEngineeringCore = Object.freeze" in core
    assert "window.autonomousEngineeringRuns = Object.freeze" in runs
    assert "window.autonomousEngineeringStatus = Object.freeze" in status
    assert "window.autonomousEngineeringControls = Object.freeze" in controls
    assert "window.autonomousEngineeringProgress = Object.freeze" in progress
    assert len(core.splitlines()) < 100
    fallback_start = orchestrator.index("function createLocalAutonomousEngineeringCore()")
    fallback_end = orchestrator.index("\n}", fallback_start) + 2
    orchestrator_logic = orchestrator[:fallback_start] + orchestrator[fallback_end:]
    assert len(orchestrator_logic.splitlines()) <= 40
    assert "renderRuns" not in orchestrator and "refreshStatus" not in orchestrator
    assert "refreshRuns" not in status and "refreshProgress" not in status
    assert "refreshProgress" not in runs and "refreshStatus" not in runs
    assert "status.refresh(true)" in controls and "autonomousEngineeringStatus" in controls
    assert "autonomous-engineering:status-mutated" in status + runs
    assert "autonomous-engineering:cleanup-completed" in runs + progress



def test_autonomous_engineering_status_can_run_when_controls_module_is_unavailable():
    orchestrator = (STATIC / "autonomous-engineering.js").read_text(encoding="utf-8")
    core = (STATIC / "autonomous-engineering-core.js").read_text(encoding="utf-8")

    assert 'if (status && status.version === 1)' in orchestrator
    assert 'if (!controls || controls.version !== 1) missing.push("کنترل");' in orchestrator
    assert 'if (!message) return;' in core
    assert 'const message = $("agent-message");\n  if (!message) return;' not in core
    assert 'if (!core || core.version !== 1)' in orchestrator


def test_task_dashboard_has_separate_view_card_and_orchestration_responsibilities():
    page = (STATIC / "index.html").read_text(encoding="utf-8")
    orchestrator = (STATIC / "tasks.js").read_text(encoding="utf-8")
    view = (STATIC / "tasks-view.js").read_text(encoding="utf-8")
    cards = (STATIC / "tasks-cards.js").read_text(encoding="utf-8")

    view_tag = page.index('src="/static/tasks-view.js"')
    cards_tag = page.index('src="/static/tasks-cards.js"')
    orchestrator_tag = page.index('src="/static/tasks.js"')
    assert view_tag < cards_tag < orchestrator_tag
    assert "createTaskView" in view and "updateTaskDashboard" in view
    assert "createTaskCards" in cards and "renderTasks" in cards
    assert "Object.defineProperty(window, \"taskCardsFactory\"" in cards
    assert "Object.defineProperty(window, \"taskViewFactory\"" in view
    assert "refreshTasks" in orchestrator and "taskCardsFactory" in orchestrator
    assert "Object.defineProperty(window,\"taskDashboard\"" in orchestrator
    assert len(orchestrator.splitlines()) < 120
    assert len(view.splitlines()) < 130
    assert len(cards.splitlines()) < 280
