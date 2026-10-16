from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app import cloud_resource_routes, cloud_resources, db, web_sources
from app.kaggle_quota import KaggleQuotaSnapshot, ResourceQuota
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
    assert response.json() == {"latest_run": None, "candidates": []}


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
