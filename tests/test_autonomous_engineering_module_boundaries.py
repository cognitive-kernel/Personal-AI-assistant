from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "static"


def _read(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


def test_autonomous_engineering_scripts_load_in_dependency_order():
    page = _read("autonomous-engineering.html")
    ordered = [
        "autonomous-engineering-core.js",
        "autonomous-engineering-runs.js",
        "autonomous-engineering-status.js",
        "autonomous-engineering-controls.js",
        "autonomous-engineering-progress.js",
        "autonomous-engineering.js",
    ]
    positions = [page.index(f'src="/static/{name}"') for name in ordered]
    assert positions == sorted(positions)
    for name in ordered:
        assert f'<script src="/static/{name}" defer></script>' in page


def test_autonomous_engineering_features_have_versioned_single_owner_contracts():
    core = _read("autonomous-engineering-core.js")
    status = _read("autonomous-engineering-status.js")
    controls = _read("autonomous-engineering-controls.js")
    runs = _read("autonomous-engineering-runs.js")
    progress = _read("autonomous-engineering-progress.js")
    shell = _read("autonomous-engineering.js")

    assert "window.autonomousEngineeringCore = Object.freeze({" in core
    assert "version: 1, $, request, setMessage, setStatusMessage, startPolling" in core
    assert "window.autonomousEngineeringStatus = Object.freeze({" in status
    assert "window.autonomousEngineeringControls = Object.freeze({version: 1, stop})" in controls
    assert "window.autonomousEngineeringRuns = Object.freeze({version: 1" in runs
    assert "window.autonomousEngineeringProgress = Object.freeze({version: 1" in progress

    assert "window.autonomousEngineeringCore" in status
    assert "window.autonomousEngineeringStatus" in controls
    assert "window.autonomousEngineeringCore" in runs
    assert "window.autonomousEngineeringCore" in progress
    assert len(shell.splitlines()) <= 100
    assert "fetch(" not in shell
    assert 'window.addEventListener("pagehide"' in shell
    assert 'window.addEventListener("pageshow"' in shell



def test_autonomous_engineering_transport_sends_secure_correlation_ids_and_keeps_them_on_timeout():
    core = _read("autonomous-engineering-core.js")

    assert "function createCorrelationId()" in core
    assert "cryptoApi.randomUUID()" in core
    assert "cryptoApi.getRandomValues(bytes)" in core
    assert "const bytes = new Uint8Array(16)" in core
    assert 'requestHeaders.set("X-Correlation-ID", correlationId)' in core
    assert "failure.correlationId = payload.correlation_id || response.headers.get(\"X-Correlation-ID\") || correlationId" in core
    assert "timeoutError.correlationId = correlationId" in core
    assert 'if (error && typeof error === "object" && !Number.isInteger(error.status))' in core
    assert 'error.message = (errorMessage ? errorMessage + " · " : "") + "شناسهٔ پیگیری: " + correlationId' in core
    assert "شناسهٔ پیگیری: " in core


def test_autonomous_engineering_status_refresh_and_controls_remain_isolated():
    status = _read("autonomous-engineering-status.js")
    controls = _read("autonomous-engineering-controls.js")

    assert "async function refreshStatus()" in status
    assert "statusRefreshPromise" in status
    assert "async function refreshAfterMutation()" in status
    assert "dispatchPendingUntil" in status
    assert "function canStartManualTask()" in status
    assert 'request("settings"' not in status
    assert 'request("dispatch"' not in status
    assert 'taskForm.addEventListener("submit"' in controls
    assert 'request("dispatch"' in controls
    assert "status.canStartManualTask()" in controls
    assert "pending_task: " in status
    assert "pending_owner: " in status


def test_autonomous_engineering_run_history_discards_stale_responses():
    runs = _read("autonomous-engineering-runs.js")
    assert "let runsRequestSequence = 0" in runs
    assert "const requestSequence = ++runsRequestSequence" in runs
    assert "if (requestSequence !== runsRequestSequence) return" in runs


def test_autonomous_engineering_progress_queues_user_refresh_and_rejects_stale_ranges():
    progress = _read("autonomous-engineering-progress.js")
    assert "let progressBusy = false" in progress
    assert "let progressRefreshQueued = false" in progress
    assert "const selectionIsCurrent = () =>" in progress
    assert "if (!selectionIsCurrent())" in progress
    assert "if (progressRefreshQueued)" in progress
    assert 'refreshProgress({queueIfBusy: true})' in progress


def test_autonomous_engineering_ui_has_no_cross_module_inner_html_rendering():
    for name in (
        "autonomous-engineering-core.js",
        "autonomous-engineering-status.js",
        "autonomous-engineering-controls.js",
        "autonomous-engineering-runs.js",
        "autonomous-engineering-progress.js",
        "autonomous-engineering.js",
    ):
        assert "innerHTML" not in _read(name)


def test_autonomous_engineering_selector_contract_matches_the_page():
    page = _read("autonomous-engineering.html")
    ids = set(re.findall(r'id="([^"]+)"', page))
    used = set()
    for name in (
        "autonomous-engineering-core.js",
        "autonomous-engineering-status.js",
        "autonomous-engineering-controls.js",
        "autonomous-engineering-runs.js",
        "autonomous-engineering-progress.js",
    ):
        source = _read(name)
        used.update(re.findall(r'\$\("([^"]+)"\)', source))
    assert not used - ids, f"UI module selectors missing from HTML: {sorted(used - ids)}"

 
def test_autonomous_engineering_run_log_safety_has_a_single_backend_owner():
    backend = (ROOT / "app" / "autonomous_engineering_run_log_safety.py").read_text(encoding="utf-8")
    facade = (ROOT / "app" / "autonomous_engineering.py").read_text(encoding="utf-8")

    assert "def sanitize_workflow_log(" in backend
    assert "def workflow_failure_summary(" in backend
    assert "def _sanitize_workflow_log(" in facade
    assert "def _workflow_failure_summary(" in facade
    assert "_run_log_safety.sanitize_workflow_log(" in facade
    assert "_run_log_safety.workflow_failure_summary(" in facade


def test_autonomous_engineering_run_history_has_a_single_backend_owner():
    backend = (ROOT / "app" / "autonomous_engineering_run_history.py").read_text(encoding="utf-8")
    facade = (ROOT / "app" / "autonomous_engineering.py").read_text(encoding="utf-8")

    for function in (
        "def cached_run_diagnostics(",
        "def workflow_runs(",
        "def verified_workflow_run(",
        "def read_run_diagnostics(",
    ):
        assert function in backend
    for function in (
        "def _cached_run_diagnostics(",
        "def _workflow_runs(",
        "def _verified_workflow_run(",
        "def _read_run_diagnostics(",
    ):
        assert function in facade
    assert "_run_history.workflow_runs(" in facade
    assert "_run_history.verified_workflow_run(" in facade



def test_autonomous_engineering_settings_have_a_single_backend_owner():
    backend = (ROOT / "app" / "autonomous_engineering_settings.py").read_text(encoding="utf-8")
    facade = (ROOT / "app" / "autonomous_engineering.py").read_text(encoding="utf-8")

    assert "def save_settings(" in backend
    assert "def save_autonomous_engineering_settings(" in facade
    assert "_settings_backend.save_settings(" in facade
    assert "save_repository_variables=_save_repository_variables" in facade
    assert "ollama_status=_ollama_status" in facade



def test_autonomous_engineering_readiness_payload_has_a_single_backend_owner():
    backend = (ROOT / "app" / "autonomous_engineering_readiness.py").read_text(encoding="utf-8")
    facade = (ROOT / "app" / "autonomous_engineering.py").read_text(encoding="utf-8")

    assert "def build_status_payload(" in backend
    assert "def collect_status(" in facade
    assert "_readiness_backend.build_status_payload(" in facade
    assert "ThreadPoolExecutor(" not in backend
    assert "BoundedSemaphore(" not in backend



def test_autonomous_engineering_run_log_payload_has_a_single_backend_owner():
    backend = (ROOT / "app" / "autonomous_engineering_run_logs.py").read_text(encoding="utf-8")
    facade = (ROOT / "app" / "autonomous_engineering.py").read_text(encoding="utf-8")

    assert "def build_run_logs_payload(" in backend
    assert "def autonomous_engineering_run_logs(" in facade
    assert "_run_logs_backend.build_run_logs_payload(" in facade
    assert "_github_api_json(gh," in facade
    assert "_verified_workflow_run(gh, run_id)" in facade



def test_autonomous_engineering_pr_preflight_has_a_single_backend_owner():
    backend = (ROOT / "app" / "autonomous_engineering_pr_preflight.py").read_text(encoding="utf-8")
    facade = (ROOT / "app" / "autonomous_engineering.py").read_text(encoding="utf-8")

    assert "def has_open_autonomous_pr(" in backend
    assert "def _has_open_autonomous_pr(" in facade
    assert "_pr_preflight.has_open_autonomous_pr(" in facade
    assert "run_command=_run" in facade



def test_autonomous_engineering_progress_projection_is_a_pure_backend():
    backend = (ROOT / "app" / "autonomous_engineering_progress_projection.py").read_text(encoding="utf-8")
    facade = (ROOT / "app" / "autonomous_engineering.py").read_text(encoding="utf-8")

    assert "def enrich_progress_payload(" in backend
    assert "def _enrich_progress_from_github(" in facade
    assert "_progress_projection.enrich_progress_payload(current, jobs)" in facade
    assert "subprocess" not in backend
    assert "import requests" not in backend


def _autonomous_engineering_python_import_graph():
    import ast
    import importlib.util

    app_dir = ROOT / "app"
    module_paths = {
        f"app.{path.stem}": path
        for path in app_dir.glob("autonomous_engineering*.py")
    }
    graph = {module: set() for module in module_paths}

    for module, path in module_paths.items():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in graph:
                        graph[module].add(alias.name)
                continue
            if not isinstance(node, ast.ImportFrom):
                continue

            if node.level:
                relative_name = "." * node.level + (node.module or "")
                base = importlib.util.resolve_name(relative_name, "app")
                candidates = (
                    [f"{base}.{alias.name}" for alias in node.names]
                    if node.module is None
                    else [base]
                )
            else:
                base = node.module or ""
                candidates = (
                    [f"{base}.{alias.name}" for alias in node.names]
                    if base == "app"
                    else [base]
                )
            graph[module].update(candidate for candidate in candidates if candidate in graph)

    return module_paths, graph


def test_autonomous_engineering_backend_import_graph_is_acyclic():
    _, graph = _autonomous_engineering_python_import_graph()
    visiting = []
    visited = set()
    cycles = []

    def visit(module):
        if module in visiting:
            start = visiting.index(module)
            cycles.append(visiting[start:] + [module])
            return
        if module in visited:
            return
        visiting.append(module)
        for dependency in sorted(graph[module]):
            visit(dependency)
        visiting.pop()
        visited.add(module)

    for module in sorted(graph):
        visit(module)

    assert not cycles, "Autonomous Engineering import cycles: " + repr(cycles)


def test_autonomous_engineering_feature_modules_do_not_import_the_router_facade():
    facade = "app.autonomous_engineering"
    _, graph = _autonomous_engineering_python_import_graph()
    violations = {
        module: sorted(dependencies & {facade})
        for module, dependencies in graph.items()
        if module != facade and facade in dependencies
    }
    assert not violations, (
        "Feature/domain modules must not import the FastAPI router facade: "
        + repr(violations)
    )


def test_autonomous_engineering_contracts_remain_dependency_independent():
    import ast

    contracts_path = ROOT / "app" / "autonomous_engineering_contracts.py"
    tree = ast.parse(contracts_path.read_text(encoding="utf-8"), filename=str(contracts_path))
    forbidden_roots = {
        "fastapi", "requests", "httpx", "subprocess", "psutil",
        "ollama", "github", "app",
    }
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".", 1)[0])

    violations = sorted(imports & forbidden_roots)
    assert not violations, (
        "Shared API contracts must not depend on transport/provider/runtime modules: "
        + repr(violations)
    )


def test_autonomous_engineering_private_cross_module_imports_are_explicitly_scoped():
    import ast

    app_dir = ROOT / "app"
    module_paths = {
        f"app.{path.stem}": path
        for path in app_dir.glob("autonomous_engineering*.py")
    }
    allowed_compatibility_imports = {
        (
            "app.autonomous_engineering",
            "app.autonomous_engineering_errors",
            "_structured_error_response",
        ),
    }
    violations = []
    for source_module, path in module_paths.items():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or not node.module:
                continue
            if node.level:
                import importlib.util
                target_module = importlib.util.resolve_name(
                    "." * node.level + node.module, "app"
                )
            else:
                target_module = node.module
            if target_module not in module_paths:
                continue
            for alias in node.names:
                if alias.name.startswith("_") and (
                    source_module, target_module, alias.name
                ) not in allowed_compatibility_imports:
                    violations.append(
                        (source_module, target_module, alias.name)
                    )

    assert not violations, (
        "Private cross-module imports require an explicit compatibility exception: "
        + repr(violations)
    )



def test_workflow_control_owns_dispatch_and_cancel_implementation():
    import ast
    facade = ast.parse((ROOT / "app" / "autonomous_engineering.py").read_text(encoding="utf-8"))
    control = ast.parse((ROOT / "app" / "autonomous_engineering_control.py").read_text(encoding="utf-8"))
    functions = {node.name: node for node in facade.body if isinstance(node, ast.FunctionDef)}
    for name in ("_dispatch_with_admission", "_dispatch_autonomous_engineering_locked", "_cancel_autonomous_engineering_locked", "_set_autonomous_engineering_pause"):
        assert len(functions[name].body) == 1
        assert isinstance(functions[name].body[0], ast.Return)
    assert {"dispatch_with_admission", "dispatch_locked", "cancel_locked", "set_pause"} <= {
        node.name for node in control.body if isinstance(node, ast.FunctionDef)
    }
