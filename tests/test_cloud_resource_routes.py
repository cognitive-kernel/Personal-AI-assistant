from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app import cloud_resource_routes, cloud_resources, db, web_sources
from app.kaggle_quota import KaggleQuotaSnapshot, ResourceQuota
from app.oci_quota import OciQuotaSnapshot, OciServiceLimit
from app.main import app


def _configure(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    monkeypatch.setattr(
        db.defaults, "cloud_resources_db_path", str(tmp_path / "cloud_resources.db")
    )
    monkeypatch.setattr(
        cloud_resources,
        "ensure_daily_discovery_scheduled",
        lambda: {"status": "disabled_for_test"},
    )


def test_cloud_resource_api_lists_candidates_and_latest_run(tmp_path, monkeypatch):
    _configure(tmp_path, monkeypatch)
    with TestClient(app, client=("127.0.0.1", 12345)) as client:
        response = client.get("/api/cloud-resources", params={"limit": 10})

    assert response.status_code == 200
    body = response.json()
    assert body["latest_run"] is None
    assert body["candidates"] == []
    assert body["pagination"] == {
        "page": 1, "page_size": 10, "total": None, "pages": None,
        "has_previous": False, "has_next": False,
    }


def test_cloud_resource_api_queues_manual_discovery_without_executing_inline(
    tmp_path, monkeypatch
):
    _configure(tmp_path, monkeypatch)
    def fake_discover(*, checkpoint=None):
        return {
            "run_id": 1,
            "status": "completed",
            "queries_attempted": 3,
            "sources_seen": 1,
            "candidates_upserted": 1,
            "error_summary": None,
        }
    monkeypatch.setattr(cloud_resources, "discover_resources", fake_discover)

    with TestClient(app, client=("127.0.0.1", 12345)) as client:
        response = client.post("/api/cloud-resources/discover")

    assert response.status_code == 202
    body = response.json()
    assert body["task"]["task_type"] == cloud_resources.CLOUD_DISCOVERY_TASK_TYPE
    assert body["task"]["payload"] == {"trigger": "manual"}
    assert "remain unreviewed" in body["message"]


def test_cloud_resource_api_rejects_non_loopback_clients(tmp_path, monkeypatch):
    _configure(tmp_path, monkeypatch)
    with TestClient(app, client=("192.0.2.10", 12345)) as client:
        response = client.get("/api/cloud-resources")

    assert response.status_code == 403



def test_cloud_resource_review_api_requires_current_hash_and_terms_url(
    tmp_path, monkeypatch
):
    _configure(tmp_path, monkeypatch)
    document = web_sources.SourceDocument(
        url="https://compute.example.org/free-tier",
        title="Free GPU compute",
        text=("Free tier includes 2 vCPU and 8 GB RAM. Read terms before use. " * 4),
    )
    monkeypatch.setattr(
        web_sources, "discover_sources", lambda query, limit=3: [document]
    )
    cloud_resources.discover_resources()
    candidate = cloud_resources.list_candidates()[0]

    with TestClient(app, client=("127.0.0.1", 12345)) as client:
        approved = client.patch(
            f"/api/cloud-resources/{candidate['id']}/review",
            json={
                "expected_content_sha256": candidate["content_sha256"],
                "review_status": "approved",
                "availability_status": "available",
                "quota": {"cpu": "2 vCPU"},
                "terms_url": "https://compute.example.org/terms",
            },
        )
        assert approved.status_code == 200
        assert approved.json()["candidate"]["review_status"] == "approved"
        assert approved.json()["candidate"]["reviewed_content_sha256"] == candidate["content_sha256"]
        refreshed = client.get("/api/cloud-resources").json()["candidates"]
        cpu_claim = next(
            claim for claim in refreshed[0]["observed_claims"]
            if claim["claim_type"] == "cpu_count"
        )
        claim_review = client.patch(
            f"/api/cloud-resources/{candidate['id']}/claims/{cpu_claim['id']}/review",
            json={
                "expected_source_content_sha256": candidate["content_sha256"],
                "review_status": "accepted",
            },
        )
        assert claim_review.status_code == 200
        assert claim_review.json()["claim"]["review_status"] == "accepted"

        stale_claim = client.patch(
            f"/api/cloud-resources/{candidate['id']}/claims/{cpu_claim['id']}/review",
            json={
                "expected_source_content_sha256": "0" * 64,
                "review_status": "rejected",
            },
        )
        assert stale_claim.status_code == 409

        stale_hash = client.patch(
            f"/api/cloud-resources/{candidate['id']}/review",
            json={
                "expected_content_sha256": "0" * 64,
                "review_status": "approved",
                "availability_status": "available",
                "terms_url": "https://compute.example.org/terms",
            },
        )
        assert stale_hash.status_code == 409



def test_kaggle_quota_consent_is_opt_in_revocable_and_audited(tmp_path, monkeypatch):
    _configure(tmp_path, monkeypatch)
    cli = tmp_path / "kaggle"
    cli.write_text("test executable placeholder", encoding="utf-8")
    calls = []
    now = datetime.now(timezone.utc)
    snapshot = KaggleQuotaSnapshot(
        observed_at=now,
        refresh_at=now + timedelta(days=1),
        gpu=ResourceQuota(used_hours=1.0, remaining_hours=9.0, total_hours=10.0),
        tpu=None,
    )

    def fake_fetch(**kwargs):
        calls.append(kwargs)
        return snapshot

    monkeypatch.setattr(cloud_resource_routes, "fetch_quota_snapshot", fake_fetch)
    with TestClient(app, client=("127.0.0.1", 12345)) as client:
        initial = client.get("/api/cloud-resources/kaggle-quota")
        assert initial.status_code == 200
        assert initial.json()["consent"]["enabled"] is False
        assert initial.json()["snapshots"] == []

        disabled_refresh = client.post("/api/cloud-resources/kaggle-quota/refresh")
        assert disabled_refresh.status_code == 409
        assert calls == []

        invalid_consent = client.patch(
            "/api/cloud-resources/kaggle-quota/consent",
            json={"enabled": True, "user_consent": True, "executable_path": "kaggle"},
        )
        assert invalid_consent.status_code == 422

        granted = client.patch(
            "/api/cloud-resources/kaggle-quota/consent",
            json={
                "enabled": True,
                "user_consent": True,
                "executable_path": str(cli.resolve()),
            },
        )
        assert granted.status_code == 200
        assert granted.json()["consent"]["enabled"] is True
        assert granted.json()["consent"]["user_consent"] is True
        assert granted.json()["consent_events"][0]["event_type"] == "consent_granted"

        refreshed = client.post("/api/cloud-resources/kaggle-quota/refresh")
        assert refreshed.status_code == 200
        assert refreshed.json()["snapshots"][0]["resource"] == "GPU"
        assert refreshed.json()["snapshots"][0]["remaining_hours"] == 9.0
        assert calls[0]["enabled"] is True and calls[0]["user_consent"] is True
        assert calls[0]["executable_path"] == str(cli.resolve())

        revoked = client.patch(
            "/api/cloud-resources/kaggle-quota/consent",
            json={"enabled": False, "user_consent": False, "executable_path": ""},
        )
        assert revoked.status_code == 200
        body = revoked.json()
        assert body["consent"]["enabled"] is False
        assert body["consent"]["user_consent"] is False
        assert body["snapshots"] == []
        assert [event["event_type"] for event in body["consent_events"][:2]] == [
            "consent_revoked",
            "consent_granted",
        ]


def test_kaggle_quota_endpoints_require_loopback_clients(tmp_path, monkeypatch):
    _configure(tmp_path, monkeypatch)
    with TestClient(app, client=("192.0.2.10", 12345)) as client:
        status_response = client.get("/api/cloud-resources/kaggle-quota")
        consent_response = client.patch(
            "/api/cloud-resources/kaggle-quota/consent",
            json={"enabled": True, "user_consent": True, "executable_path": "/tmp/kaggle"},
        )
        refresh_response = client.post("/api/cloud-resources/kaggle-quota/refresh")

    assert status_response.status_code == 403
    assert consent_response.status_code == 403
    assert refresh_response.status_code == 403


def test_kaggle_quota_consent_requires_json_booleans_not_coercible_values(tmp_path, monkeypatch):
    _configure(tmp_path, monkeypatch)
    with TestClient(app, client=("127.0.0.1", 12345)) as client:
        response = client.patch(
            "/api/cloud-resources/kaggle-quota/consent",
            json={"enabled": 1, "user_consent": True, "executable_path": "/tmp/kaggle"},
        )

    assert response.status_code == 422



def test_oci_limit_consent_refresh_and_revocation_are_audited(tmp_path, monkeypatch):
    _configure(tmp_path, monkeypatch)
    cli = tmp_path / "oci"
    config = tmp_path / "oci-config"
    cli.write_text("test executable placeholder", encoding="utf-8")
    config.write_text("[DEFAULT]\n", encoding="utf-8")
    calls = []
    now = datetime.now(timezone.utc)
    snapshot = OciQuotaSnapshot(
        observed_at=now,
        region="us-ashburn-1",
        limits=(
            OciServiceLimit(
                name="standard-a1-core-count",
                value=2,
                used=1,
                available=1,
                scope_type="REGION",
                scope_value="us-ashburn-1",
            ),
        ),
    )

    def fake_fetch(**kwargs):
        calls.append(kwargs)
        return snapshot

    monkeypatch.setattr(cloud_resource_routes, "fetch_service_limits", fake_fetch)
    with TestClient(app, client=("127.0.0.1", 12345)) as client:
        initial = client.get("/api/cloud-resources/oci-limits")
        assert initial.status_code == 200
        assert initial.json()["consent"]["enabled"] is False
        assert initial.json()["snapshots"] == []
        assert initial.json()["execution_authorized"] is False

        disabled = client.post("/api/cloud-resources/oci-limits/refresh")
        assert disabled.status_code == 409
        assert calls == []

        invalid = client.patch(
            "/api/cloud-resources/oci-limits/consent",
            json={
                "enabled": True,
                "user_consent": True,
                "executable_path": "oci",
                "config_file": str(config.resolve()),
                "profile": "DEFAULT",
                "region": "us-ashburn-1",
                "compartment_id": "ocid1.compartment.oc1..aaaaaaaaexample",
            },
        )
        assert invalid.status_code == 422

        granted = client.patch(
            "/api/cloud-resources/oci-limits/consent",
            json={
                "enabled": True,
                "user_consent": True,
                "executable_path": str(cli.resolve()),
                "config_file": str(config.resolve()),
                "profile": "DEFAULT",
                "region": "us-ashburn-1",
                "compartment_id": "ocid1.compartment.oc1..aaaaaaaaexample",
            },
        )
        assert granted.status_code == 200
        assert granted.json()["consent"]["enabled"] is True
        assert granted.json()["consent"]["user_consent"] is True
        assert granted.json()["consent_events"][0]["event_type"] == "consent_granted"

        refreshed = client.post("/api/cloud-resources/oci-limits/refresh")
        assert refreshed.status_code == 200
        body = refreshed.json()
        assert body["execution_authorized"] is False
        assert body["snapshots"][0]["resource_name"] == "standard-a1-core-count"
        assert body["snapshots"][0]["limit_value"] == 2
        assert body["snapshots"][0]["available_value"] == 1
        assert calls[0]["enabled"] is True and calls[0]["user_consent"] is True
        assert calls[0]["config_file"] == str(config.resolve())
        assert "private_key" not in str(body).casefold()

        revoked = client.patch(
            "/api/cloud-resources/oci-limits/consent",
            json={
                "enabled": False,
                "user_consent": False,
                "executable_path": "",
                "config_file": "",
                "profile": "",
                "region": "",
                "compartment_id": "",
            },
        )
        assert revoked.status_code == 200
        assert revoked.json()["consent"]["enabled"] is False
        assert revoked.json()["consent"]["config_file"] == ""
        assert revoked.json()["snapshots"] == []
        assert [event["event_type"] for event in revoked.json()["consent_events"][:2]] == [
            "consent_revoked",
            "consent_granted",
        ]


def test_oci_limit_endpoints_require_loopback_clients(tmp_path, monkeypatch):
    _configure(tmp_path, monkeypatch)
    with TestClient(app, client=("192.0.2.10", 12345)) as client:
        status_response = client.get("/api/cloud-resources/oci-limits")
        consent_response = client.patch(
            "/api/cloud-resources/oci-limits/consent",
            json={"enabled": True, "user_consent": True},
        )
        refresh_response = client.post("/api/cloud-resources/oci-limits/refresh")

    assert status_response.status_code == 403
    assert consent_response.status_code == 403
    assert refresh_response.status_code == 403


def test_oci_limit_consent_requires_strict_json_booleans(tmp_path, monkeypatch):
    _configure(tmp_path, monkeypatch)
    with TestClient(app, client=("127.0.0.1", 12345)) as client:
        response = client.patch(
            "/api/cloud-resources/oci-limits/consent",
            json={"enabled": 1, "user_consent": True},
        )
    assert response.status_code == 422



def test_resource_plan_api_is_loopback_only_and_never_authorizes_execution(tmp_path, monkeypatch):
    _configure(tmp_path, monkeypatch)
    with TestClient(app, client=("127.0.0.1", 12345)) as client:
        response = client.post(
            "/api/cloud-resources/plan",
            json={
                "min_cpu_count": 2,
                "min_memory_gib": 8,
                "require_gpu": True,
                "gpu_models": ["NVIDIA A10"],
                "allowed_provider_domains": ["compute.example.org"],
            },
        )
        blocked = TestClient(app, client=("192.0.2.10", 12345)).post(
            "/api/cloud-resources/plan",
            json={},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "no_eligible_candidate"
    assert body["selected_candidate_id"] is None
    assert body["execution_authorized"] is False
    assert body["remote_execution_enabled"] is False
    assert body["candidates"] == []
    assert blocked.status_code == 403


def test_resource_plan_api_rejects_invalid_requirements_without_side_effects(tmp_path, monkeypatch):
    _configure(tmp_path, monkeypatch)
    with TestClient(app, client=("127.0.0.1", 12345)) as client:
        response = client.post(
            "/api/cloud-resources/plan",
            json={"min_cpu_count": True},
        )

    assert response.status_code == 422
    assert "min_cpu_count" in response.json()["detail"]



def test_resource_plan_api_reports_eligible_candidate_and_rejects_stale_evidence(
    tmp_path, monkeypatch
):
    _configure(tmp_path, monkeypatch)
    candidate = {
        "id": 41,
        "provider_domain": "compute.example.org",
        "content_sha256": "a" * 64,
        "reviewed_content_sha256": "a" * 64,
        "review_status": "approved",
        "freshness_status": "fresh",
        "availability_status": "available",
        "terms_url": "https://compute.example.org/terms",
        "quota": {
            "max_cost_usd": 0,
            "auth_method": "anonymous",
            "selected_region": "us-east-1",
            "allowed_regions": ["us-east-1"],
            "max_session_minutes": 60,
            "max_monthly_hours": 100,
        },
        "observed_claims": [
            {"claim_type": "cpu_count", "value": 4, "unit": None,
             "source_content_sha256": "a" * 64, "review_status": "accepted", "is_current": 1},
            {"claim_type": "memory_capacity", "value": 16, "unit": "GiB",
             "source_content_sha256": "a" * 64, "review_status": "accepted", "is_current": 1},
            {"claim_type": "usage_quota", "value": "100 hours", "unit": None,
             "source_content_sha256": "a" * 64, "review_status": "accepted", "is_current": 1},
            {"claim_type": "free_tier_mention", "value": "free tier", "unit": None,
             "source_content_sha256": "a" * 64, "review_status": "accepted", "is_current": 1},
            {"claim_type": "region", "value": "us-east-1", "unit": None,
             "source_content_sha256": "a" * 64, "review_status": "accepted", "is_current": 1},
        ],
    }
    monkeypatch.setattr(cloud_resources, "list_candidates", lambda limit=100: [candidate])

    with TestClient(app, client=("127.0.0.1", 12345)) as client:
        eligible = client.post(
            "/api/cloud-resources/plan",
            json={"min_cpu_count": 2, "min_memory_gib": 8},
        )
        candidate["freshness_status"] = "stale"
        stale = client.post(
            "/api/cloud-resources/plan",
            json={"min_cpu_count": 2, "min_memory_gib": 8},
        )

    assert eligible.status_code == 200
    assert eligible.json()["status"] == "candidate_found"
    assert eligible.json()["selected_candidate_id"] == 41
    assert eligible.json()["execution_authorized"] is False
    assert eligible.json()["remote_execution_enabled"] is False
    assert stale.status_code == 200
    assert stale.json()["status"] == "no_eligible_candidate"
    assert stale.json()["selected_candidate_id"] is None
    assert any(
        "stale" in reason.lower()
        for reason in stale.json()["candidates"][0]["policy_blockers"]
    )



def test_resource_plan_api_persists_and_lists_dry_run_history(tmp_path, monkeypatch):
    _configure(tmp_path, monkeypatch)
    with TestClient(app, client=("127.0.0.1", 12345)) as client:
        created = client.post(
            "/api/cloud-resources/plan",
            json={"min_cpu_count": 3, "min_memory_gib": 12},
        )
        assert created.status_code == 200
        plan = created.json()
        assert plan["plan_id"] > 0
        assert plan["execution_authorized"] is False
        assert plan["remote_execution_enabled"] is False

        fetched = client.get(f"/api/cloud-resources/plans/{plan['plan_id']}")
        listed = client.get("/api/cloud-resources/plans")
        missing = client.get("/api/cloud-resources/plans/99999")

    assert fetched.status_code == 200
    assert fetched.json()["plan_id"] == plan["plan_id"]
    assert fetched.json()["requirements"]["min_cpu_count"] == 3
    assert fetched.json()["limitations"] == plan["limitations"]
    assert fetched.json()["execution_authorized"] is False
    assert listed.status_code == 200
    assert listed.json()["plans"][0]["plan_id"] == plan["plan_id"]
    assert listed.json()["remote_execution_enabled"] is False
    assert missing.status_code == 404
