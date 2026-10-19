from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime

import pytest

from app import cloud_resources, db, task_manager, web_sources
from app.cloud_resources import CloudResourceError
from app.schema_migrations import MigrationRunner
from app.schema_registry import CLOUD_RESOURCE_MIGRATIONS, initialize_database_file


def _configure_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    monkeypatch.setattr(
        db.defaults, "cloud_resources_db_path", str(tmp_path / "cloud_resources.db")
    )
    db.init_databases()
    task_manager.initialize_task_store()
    cloud_resources.initialize_store()


def _document(url="https://compute.example.org/free-tier", title="Free GPU compute", text=None):
    return web_sources.SourceDocument(
        url=url,
        title=title,
        text=text or (
            "Free tier GPU compute credits and quota limits. Read the official terms "
            "and usage policy before choosing a provider. " * 4
        ),
    )


def test_discovery_persists_per_provider_search_diagnostics(tmp_path, monkeypatch):
    _configure_paths(tmp_path, monkeypatch)
    document = _document()
    diagnostics = [
        {
            "provider": "Google",
            "attempts": [{
                "variant": "primary",
                "parsed_result_count": 3,
                "accepted_result_count": 3,
                "unique_result_count": 2,
                "duplicate_result_count": 1,
                "error": None,
            }],
            "parsed_result_count": 3,
            "accepted_result_count": 3,
            "unique_result_count": 2,
            "duplicate_result_count": 1,
        }
    ]
    documents = web_sources.SourceDocumentList(
        [document], search_provider_diagnostics=diagnostics
    )
    monkeypatch.setattr(
        web_sources, "discover_sources", lambda query, limit=3: documents
    )

    report = cloud_resources.discover_resources()
    latest = cloud_resources.latest_discovery_run()

    assert report["status"] == "completed"
    assert latest["query_results"][0]["provider_diagnostics"] == diagnostics


def test_discovery_scope_is_broader_but_has_explicit_bounds():
    assert len(cloud_resources.DISCOVERY_QUERIES) == 5
    assert any("cloud hosting" in query for query in cloud_resources.DISCOVERY_QUERIES)
    assert cloud_resources.MAX_RESULTS_PER_QUERY == 5
    assert cloud_resources.MAX_CANDIDATES_PER_RUN == (
        len(cloud_resources.DISCOVERY_QUERIES)
        * cloud_resources.MAX_RESULTS_PER_QUERY
    )


def test_free_service_catalog_without_a_concrete_compute_offer_is_rejected():
    document = web_sources.SourceDocument(
        url="https://azure.microsoft.com/en-us/pricing/free-services/",
        title="Explore Free Azure Services",
        text=(
            "These services are free up to the specified monthly amounts.\n"
            "Linux virtual machines are available in Azure.\n"
            "Get access to free developer tools and a USD 100 credit.\n"
            "What is a container? See the terms of use."
        ) * 4,
    )

    signals = cloud_resources._resource_signals(document.title, document.text)

    assert "vm_or_container_mentioned" in signals
    assert "free_tier_or_credits_mentioned" in signals
    assert "specific_compute_offer_pattern_matched" not in signals
    assert cloud_resources._candidate_record(document, "free Azure services") is None


def test_official_always_free_compute_document_is_still_a_candidate():
    document = web_sources.SourceDocument(
        url="https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm",
        title="Always Free Resources",
        text=(
            "All tenancies get a set of Always Free resources in the Compute service "
            "for creating compute virtual machine instances.\n"
            "All tenancies get the first 1,500 OCPU hours and 9,000 GB hours per month "
            "for free for VM instances using the VM.Standard.A1.Flex shape."
        ) * 3,
    )

    assert cloud_resources._candidate_record(document, "official always free virtual machine") is not None


def test_legacy_free_service_catalog_is_hidden_after_contextual_recheck(
    tmp_path, monkeypatch
):
    _configure_paths(tmp_path, monkeypatch)
    document = _document()
    monkeypatch.setattr(
        web_sources, "discover_sources", lambda query, limit=3: [document]
    )
    cloud_resources.discover_resources()

    legacy_title = "Explore Free Azure Services"
    legacy_text = (
        "These services are free up to the specified monthly amounts. "
        "Linux virtual machines are available in Azure. "
        "Get access to free developer tools and a USD 100 credit. "
        "What is a container? See the terms of use. "
    ) * 4
    legacy_signals = [
        "free_tier_or_credits_mentioned",
        "vm_or_container_mentioned",
        "terms_or_policy_mentioned",
        "specific_compute_offer_pattern_matched",
    ]
    with sqlite3.connect(db.defaults.cloud_resources_db_path) as connection:
        connection.execute(
            """UPDATE cloud_resource_candidates
               SET source_url=?, provider_domain=?, title=?,
                   evidence_excerpt=?, resource_signals_json=?
               WHERE source_url=?""",
            (
                "https://azure.microsoft.com/en-us/pricing/free-services/",
                "azure.microsoft.com",
                legacy_title,
                legacy_text,
                json.dumps(legacy_signals),
                document.url,
            ),
        )
        connection.commit()

    assert cloud_resources.list_candidates() == []


def test_free_cloud_hosting_offer_is_accepted_as_a_compute_candidate():
    document = web_sources.SourceDocument(
        url="https://hosting.example.org/free-cloud",
        title="Free Cloud Hosting Plan",
        text=(
            "Our free cloud hosting plan provides a Linux cloud server with 2 vCPU. "
            "The free plan has monthly usage quotas and runtime limits. "
            "See the service terms for restrictions. "
        ) * 5,
    )

    candidate = cloud_resources._candidate_record(document, "free cloud hosting")

    assert candidate is not None
    assert "cloud_hosting_mentioned" in json.loads(candidate["resource_signals_json"])
    assert "specific_compute_offer_pattern_matched" in json.loads(
        candidate["resource_signals_json"]
    )


def test_cloud_resource_store_uses_its_own_versioned_database(tmp_path, monkeypatch):
    _configure_paths(tmp_path, monkeypatch)

    assert db.defaults.cloud_resources_db_path != db.defaults.learning_db_path
    with sqlite3.connect(db.defaults.cloud_resources_db_path) as connection:
        tables = {
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert {
            "schema_migrations",
            "cloud_resource_candidates",
            "cloud_resource_discovery_runs",
            "cloud_resource_claims",
            "cloud_resource_selection_plans",
            "cloud_resource_selection_decisions",
        } <= tables
        assert connection.execute(
            "SELECT MAX(version) FROM schema_migrations"
        ).fetchone()[0] == 7
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_discovery_persists_provenance_and_keeps_candidates_unreviewed(
    tmp_path, monkeypatch
):
    _configure_paths(tmp_path, monkeypatch)
    calls = []

    def fake_discover(query, *, limit):
        calls.append((query, limit))
        return [_document()]

    report = cloud_resources.discover_resources(discover_fn=fake_discover)

    assert report["status"] == "completed"
    assert report["queries_attempted"] == len(cloud_resources.DISCOVERY_QUERIES)
    assert report["sources_seen"] == len(cloud_resources.DISCOVERY_QUERIES)
    assert report["candidates_upserted"] == 1
    assert all(limit == cloud_resources.MAX_RESULTS_PER_QUERY for _, limit in calls)

    candidate = cloud_resources.list_candidates()[0]
    assert candidate["provider_domain"] == "compute.example.org"
    assert candidate["source_url"] == "https://compute.example.org/free-tier"
    assert len(candidate["content_sha256"]) == 64
    assert candidate["review_status"] == "unreviewed"
    assert candidate["availability_status"] == "unknown"
    assert candidate["freshness_status"] == "fresh"
    assert candidate["reviewed_content_sha256"] is None
    assert candidate["quota"] == {}
    assert "gpu_or_accelerator_mentioned" in candidate["resource_signals"]
    assert "free_tier_or_credits_mentioned" in candidate["resource_signals"]
    assert candidate["evidence_excerpt"]
    assert any(claim["claim_type"] == "free_tier_mention" for claim in candidate["observed_claims"])
    assert all(claim["review_status"] == "unreviewed" for claim in candidate["observed_claims"])
    assert candidate["eligibility_assessment"]["status"] == "blocked"
    assert candidate["eligibility_assessment"]["execution_authorized"] is False
    latest = cloud_resources.latest_discovery_run()
    assert latest["id"] == report["run_id"]
    assert latest["status"] == "completed"
    assert len(latest["query_results"]) == len(cloud_resources.DISCOVERY_QUERIES)


def test_refresh_preserves_review_only_when_source_content_is_unchanged(
    tmp_path, monkeypatch
):
    _configure_paths(tmp_path, monkeypatch)
    first = _document(text="Free tier GPU compute quota. Official usage terms. " * 4)
    changed = _document(text=("CPU compute credits and storage quota. Official usage terms. "
                              "2 vCPU and 16 GB RAM. " * 4))
    monkeypatch.setattr(
        web_sources, "discover_sources", lambda query, limit=3: [first]
    )
    cloud_resources.discover_resources()
    with sqlite3.connect(db.defaults.cloud_resources_db_path) as connection:
        connection.execute(
            """UPDATE cloud_resource_candidates
               SET review_status='approved', availability_status='available',
                   quota_json=?, terms_url=?, reviewed_at=CURRENT_TIMESTAMP,
                   reviewed_content_sha256=content_sha256 WHERE source_url=?""",
            (json.dumps({"cpu": "2 vCPU"}), "https://compute.example.org/terms", first.url),
        )
        connection.commit()

    # Re-observing identical evidence must not erase an explicit review decision.
    cloud_resources.discover_resources()
    candidate = cloud_resources.list_candidates()[0]
    assert candidate["review_status"] == "approved"
    assert candidate["availability_status"] == "available"
    assert candidate["quota"] == {"cpu": "2 vCPU"}
    assert candidate["terms_url"] == "https://compute.example.org/terms"

    # A changed page invalidates review and quota claims tied to the old content hash.
    monkeypatch.setattr(
        web_sources, "discover_sources", lambda query, limit=3: [changed]
    )
    cloud_resources.discover_resources()
    candidate = cloud_resources.list_candidates()[0]
    assert candidate["content_sha256"] == hashlib.sha256(
        f"{changed.title}\n{' '.join(changed.text.split())}".encode()
    ).hexdigest()
    assert candidate["review_status"] == "unreviewed"
    assert candidate["availability_status"] == "unknown"
    assert candidate["quota"] == {}
    assert candidate["terms_url"] is None
    assert candidate["reviewed_at"] is None
    assert candidate["reviewed_content_sha256"] is None
    assert any(claim["claim_type"] == "cpu_count" for claim in candidate["observed_claims"])
    assert any(claim["claim_type"] == "memory_capacity" for claim in candidate["observed_claims"])
    with sqlite3.connect(db.defaults.cloud_resources_db_path) as connection:
        history = connection.execute(
            "SELECT source_content_sha256, is_current FROM cloud_resource_claims WHERE candidate_id=?",
            (candidate["id"],),
        ).fetchall()
    assert any(content_hash != candidate["content_sha256"] and current == 0 for content_hash, current in history)
    assert all(content_hash == candidate["content_sha256"] and current == 1
               for content_hash, current in history if current == 1)


def test_discovery_rejects_irrelevant_search_results(tmp_path, monkeypatch):
    _configure_paths(tmp_path, monkeypatch)
    monkeypatch.setattr(
        web_sources,
        "fetch_public_document",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            web_sources.SourceFetchError("official fallback disabled in this test")
        ),
    )
    lyric_page = web_sources.SourceDocument(
        url="https://www.lyricsfreak.com/example/song_123.html",
        title="They Don't Care About Us lyrics",
        text=(
            "Lyrics and music information for the song. The word free appears here, "
            "but this is not technical documentation about hosted infrastructure. "
        ) * 5,
    )
    monkeypatch.setattr(
        web_sources, "discover_sources", lambda query, limit=3: [lyric_page]
    )

    report = cloud_resources.discover_resources()

    assert report["status"] == "failed"
    assert report["sources_seen"] == len(cloud_resources.DISCOVERY_QUERIES)
    assert report["candidates_upserted"] == 0
    assert "No relevant valid public HTTPS cloud-compute" in report["error_summary"]
    assert cloud_resources.list_candidates() == []
    latest = cloud_resources.latest_discovery_run()
    assert latest["query_results"][0]["candidate_filter"]["documents_checked"] == 1
    assert latest["query_results"][0]["candidate_filter"]["rejected_as_irrelevant"] == 1


def test_provider_domain_recognition_uses_safe_host_boundaries():
    assert cloud_resources._is_recognized_provider_domain("docs.oracle.com")
    assert cloud_resources._is_recognized_provider_domain("www.oracle.com")
    assert not cloud_resources._is_recognized_provider_domain("shicheng-guo.github.io")
    assert not cloud_resources._is_recognized_provider_domain("oracle.com.attacker.example")


def test_candidate_source_classification_marks_likely_articles_without_claiming_verification():
    assert cloud_resources._candidate_source_classification({
        "source_url": "https://snapdeploy.dev/blog/free-cloud-deployment-platforms-2026-comparison",
        "title": "Free Cloud Hosting Platforms Compared",
    }) == "likely_intermediary_article"
    assert cloud_resources._candidate_source_classification({
        "source_url": "https://compute.example.org/pricing",
        "title": "Compute pricing",
    }) == "unclassified_unverified_domain"
    assert cloud_resources._candidate_source_classification({
        "source_url": "https://docs.oracle.com/en-us/iaas/Content/FreeTier/",
        "title": "How to use the free tier",
    }) == "recognized_provider_domain"
    assert cloud_resources._candidate_source_rank({
        "source_url": "https://compute.example.org/pricing",
        "title": "Compute pricing",
    }) < cloud_resources._candidate_source_rank({
        "source_url": "https://snapdeploy.dev/blog/free-cloud-compute-guide",
        "title": "Free cloud compute guide",
    })


def test_list_candidates_prioritizes_official_references_before_newer_articles(
    tmp_path, monkeypatch
):
    _configure_paths(tmp_path, monkeypatch)
    secondary_article = _document(
        url="https://agentdeals.dev/free-cloud-compute-guide",
        title="Free cloud compute guide",
    )
    official_reference = _document(
        url="https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm",
        title="Always Free Resources",
    )
    monkeypatch.setattr(
        web_sources,
        "discover_sources",
        lambda query, limit=3: [secondary_article, official_reference],
    )
    cloud_resources.discover_resources()

    with sqlite3.connect(db.defaults.cloud_resources_db_path) as connection:
        connection.execute(
            """UPDATE cloud_resource_candidates
               SET last_seen_at=datetime('now')
               WHERE source_url=?""",
            (secondary_article.url,),
        )
        connection.execute(
            """UPDATE cloud_resource_candidates
               SET last_seen_at=datetime('now', '-3 days')
               WHERE source_url=?""",
            (official_reference.url,),
        )
        connection.commit()

    candidates = cloud_resources.list_candidates(limit=2)

    assert [candidate["provider_domain"] for candidate in candidates] == [
        "docs.oracle.com",
        "agentdeals.dev",
    ]
    assert candidates[0]["source_authority"] == "recognized_official_domain"
    assert candidates[0]["source_classification"] == "recognized_provider_domain"
    assert candidates[1]["source_authority"] == "unverified_domain"
    assert candidates[1]["source_classification"] == "likely_intermediary_article"
    assert cloud_resources.list_candidates(limit=1)[0]["provider_domain"] == "docs.oracle.com"


def test_list_candidates_deduplicates_urls_with_tracking_and_www_variants(
    tmp_path, monkeypatch
):
    _configure_paths(tmp_path, monkeypatch)
    document = _document()
    monkeypatch.setattr(
        web_sources, "discover_sources", lambda query, limit=3: [document]
    )
    cloud_resources.discover_resources()

    duplicate_url = "https://www.compute.example.org/free-tier?utm_source=duplicate#section"
    with sqlite3.connect(db.defaults.cloud_resources_db_path) as connection:
        connection.execute(
            """INSERT INTO cloud_resource_candidates(
                   source_url, provider_domain, title, content_sha256,
                   resource_signals_json, evidence_excerpt, discovery_query
               )
               SELECT ?, provider_domain, title, content_sha256,
                      resource_signals_json, evidence_excerpt, discovery_query
               FROM cloud_resource_candidates WHERE source_url=?""",
            (duplicate_url, document.url),
        )
        connection.commit()

    candidates = cloud_resources.list_candidates()

    assert len(candidates) == 1
    assert candidates[0]["provider_domain"] == "compute.example.org"



def test_generic_ai_product_page_with_broad_signals_is_not_a_compute_candidate():
    chatgpt_page = web_sources.SourceDocument(
        url="https://openai.com/index/chatgpt/",
        title="Introducing ChatGPT | OpenAI",
        text=(
            "This article describes a conversational AI model. GPU systems may be used "
            "by AI providers. Some product features are free. Terms and policy pages "
            "describe usage limits and credits. " * 4
        ),
    )

    signals = cloud_resources._resource_signals(chatgpt_page.title, chatgpt_page.text)
    assert {
        "gpu_or_accelerator_mentioned",
        "free_tier_or_credits_mentioned",
        "quota_or_limit_mentioned",
        "terms_or_policy_mentioned",
    } <= set(signals)
    assert "specific_compute_offer_pattern_matched" not in signals
    assert cloud_resources._candidate_record(
        chatgpt_page, "official free cloud CPU virtual machine compute credits free tier"
    ) is None


def test_list_candidates_hides_legacy_ai_product_page_with_only_broad_signals(
    tmp_path, monkeypatch
):
    _configure_paths(tmp_path, monkeypatch)
    document = _document()
    monkeypatch.setattr(
        web_sources, "discover_sources", lambda query, limit=3: [document]
    )
    cloud_resources.discover_resources()

    generic_title = "Introducing ChatGPT | OpenAI"
    generic_text = (
        "This article describes a conversational AI model. GPU systems may be used "
        "by AI providers. Some product features are free. Terms and policy pages "
        "describe usage limits and credits. " * 4
    )
    signals = cloud_resources._resource_signals(generic_title, generic_text)
    # Store a row from an older version that accepted broad word-level matches.
    with sqlite3.connect(db.defaults.cloud_resources_db_path) as connection:
        connection.execute(
            """UPDATE cloud_resource_candidates
               SET source_url=?, provider_domain=?, title=?, evidence_excerpt=?,
                   resource_signals_json=?
               WHERE source_url=?""",
            (
                "https://openai.com/index/chatgpt/",
                "openai.com",
                generic_title,
                generic_text,
                json.dumps(signals),
                document.url,
            ),
        )
        connection.commit()

    assert cloud_resources.list_candidates() == []


def test_list_candidates_hides_irrelevant_rows_saved_by_older_versions(
    tmp_path, monkeypatch
):
    _configure_paths(tmp_path, monkeypatch)
    document = _document()
    monkeypatch.setattr(
        web_sources, "discover_sources", lambda query, limit=3: [document]
    )
    cloud_resources.discover_resources()

    with sqlite3.connect(db.defaults.cloud_resources_db_path) as connection:
        connection.execute(
            """UPDATE cloud_resource_candidates
               SET title=?, evidence_excerpt=?, resource_signals_json=?
               WHERE source_url=?""",
            (
                "Michael Jackson - song lyrics",
                "Song lyrics and music information, not cloud-compute documentation.",
                json.dumps(["free_tier_or_credits_mentioned"]),
                document.url,
            ),
        )
        connection.commit()

    assert cloud_resources.list_candidates() == []


def test_discovery_falls_back_to_official_references_when_search_is_irrelevant(
    tmp_path, monkeypatch
):
    _configure_paths(tmp_path, monkeypatch)
    lyric_page = web_sources.SourceDocument(
        url="https://www.lyricsfreak.com/example/song_123.html",
        title="They Don't Care About Us lyrics",
        text=(
            "Lyrics and music information. The word free appears here, "
            "but this is not technical documentation about hosted infrastructure. "
        ) * 5,
    )
    official_documents = {
        url: _document(
            url=url,
            title=title,
            text=(
                "Free compute resources include GPU or CPU environments. "
                "Review the current quota limits and usage terms. "
                "Availability depends on the account and may change. "
            ) * 4,
        )
        for url, title in cloud_resources.OFFICIAL_REFERENCE_SOURCES
    }
    monkeypatch.setattr(
        web_sources, "discover_sources", lambda query, limit=3: [lyric_page]
    )
    monkeypatch.setattr(
        web_sources,
        "fetch_public_document",
        lambda url, title_hint: official_documents[url],
    )

    report = cloud_resources.discover_resources()

    assert report["status"] == "partial"
    assert report["queries_attempted"] == len(cloud_resources.DISCOVERY_QUERIES) + 1
    assert report["candidates_upserted"] == len(cloud_resources.OFFICIAL_REFERENCE_SOURCES)
    assert "search_results_filtered_as_irrelevant" in report["error_summary"]
    assert {item["source_url"] for item in cloud_resources.list_candidates()} == set(
        official_documents
    )
    assert cloud_resources.latest_discovery_run()["query_results"][-1]["query"] == (
        "official_reference_fallback"
    )


def test_discovery_rejects_http_sources_and_records_empty_runs(tmp_path, monkeypatch):
    _configure_paths(tmp_path, monkeypatch)
    monkeypatch.setattr(
        web_sources,
        "fetch_public_document",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            web_sources.SourceFetchError("official fallback disabled in this test")
        ),
    )
    monkeypatch.setattr(
        web_sources,
        "discover_sources",
        lambda query, limit=3: [
            _document(url="http://compute.example.org/free"),
            _document(url="https://compute.example.org/short", text="Too short"),
        ],
    )

    report = cloud_resources.discover_resources()

    assert report["status"] == "failed"
    assert report["candidates_upserted"] == 0
    assert "No relevant valid public HTTPS cloud-compute" in report["error_summary"]
    assert cloud_resources.list_candidates() == []




def test_discovery_records_search_provider_failure_details(tmp_path, monkeypatch):
    _configure_paths(tmp_path, monkeypatch)
    monkeypatch.setattr(
        web_sources,
        "fetch_public_document",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            web_sources.SourceFetchError("official fallback disabled in this test")
        ),
    )

    def failed_search(_query, *, limit):
        raise web_sources.SourceFetchError(
            "All configured search providers failed or returned no parseable results "
            "(DuckDuckGo:no_parseable_results, Bing:no_parseable_results)."
        )

    monkeypatch.setattr(web_sources, "discover_sources", failed_search)

    report = cloud_resources.discover_resources()

    assert report["status"] == "failed"
    assert report["sources_seen"] == 0
    assert report["candidates_upserted"] == 0
    assert "DuckDuckGo:no_parseable_results" in report["error_summary"]
    latest = cloud_resources.latest_discovery_run()
    assert "Bing:no_parseable_results" in latest["query_results"][0]["error"]



def test_daily_discovery_enqueue_is_idempotent(tmp_path, monkeypatch):
    _configure_paths(tmp_path, monkeypatch)

    first = cloud_resources.ensure_daily_discovery_scheduled()
    second = cloud_resources.ensure_daily_discovery_scheduled()

    assert first["id"] == second["id"]
    assert first["task_type"] == cloud_resources.CLOUD_DISCOVERY_TASK_TYPE
    assert first["status"] == "queued"
    assert first["payload"] == {"trigger": "scheduled"}


def test_daily_discovery_recovers_a_pre_run_cancelled_task(tmp_path, monkeypatch):
    _configure_paths(tmp_path, monkeypatch)

    first = cloud_resources.ensure_daily_discovery_scheduled()
    cancelled = task_manager.request_cancel(first["id"])
    assert cancelled["status"] == "cancelled"

    recovery = cloud_resources.ensure_daily_discovery_scheduled()

    assert recovery["id"] != first["id"]
    assert recovery["task_type"] == cloud_resources.CLOUD_DISCOVERY_TASK_TYPE
    assert recovery["status"] == "queued"


def test_discovery_handler_schedules_next_daily_pass(tmp_path, monkeypatch):
    _configure_paths(tmp_path, monkeypatch)
    monkeypatch.setattr(
        cloud_resources,
        "discover_resources",
        lambda checkpoint=None: {
            "run_id": 1,
            "status": "completed",
            "queries_attempted": 3,
            "sources_seen": 2,
            "candidates_upserted": 2,
            "error_summary": None,
        },
    )

    class Context:
        def checkpoint(self, data):
            return data

    result = cloud_resources.handle_discovery_task(
        {"trigger": "manual"}, None, Context()
    )
    next_task = task_manager.get_task(result["next_scheduled_task_id"])
    assert next_task["task_type"] == cloud_resources.CLOUD_DISCOVERY_TASK_TYPE
    assert next_task["status"] == "queued"
    assert next_task["payload"] == {"trigger": "scheduled"}
    assert datetime.fromisoformat(result["next_scheduled_at"]).tzinfo is not None


def test_cloud_discovery_migrations_can_initialize_a_fresh_database():
    with sqlite3.connect(":memory:") as connection:
        from app.schema_migrations import MigrationRunner
        assert MigrationRunner(CLOUD_RESOURCE_MIGRATIONS).apply(connection) == 7
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"



def test_review_candidate_requires_current_hash_and_https_terms(tmp_path, monkeypatch):
    _configure_paths(tmp_path, monkeypatch)
    document = _document()
    monkeypatch.setattr(
        web_sources, "discover_sources", lambda query, limit=3: [document]
    )
    cloud_resources.discover_resources()
    candidate = cloud_resources.list_candidates()[0]

    reviewed = cloud_resources.review_candidate(
        candidate["id"],
        expected_content_sha256=candidate["content_sha256"],
        review_status="approved",
        availability_status="available",
        quota={"cpu": "2 vCPU", "hours_per_month": 100},
        terms_url="https://compute.example.org/terms",
    )
    assert reviewed["review_status"] == "approved"
    assert reviewed["availability_status"] == "available"
    assert reviewed["reviewed_content_sha256"] == candidate["content_sha256"]
    assert reviewed["reviewed_at"]
    assert reviewed["quota"] == {"cpu": "2 vCPU", "hours_per_month": 100}

    with pytest.raises(cloud_resources.CloudResourceConflictError, match="content changed"):
        cloud_resources.review_candidate(
            candidate["id"],
            expected_content_sha256="0" * 64,
            review_status="approved",
            availability_status="available",
            terms_url="https://compute.example.org/terms",
        )
    with pytest.raises(cloud_resources.CloudResourceError, match="HTTPS"):
        cloud_resources.review_candidate(
            candidate["id"],
            expected_content_sha256=candidate["content_sha256"],
            review_status="approved",
            availability_status="available",
            terms_url="http://compute.example.org/terms",
        )


def test_stale_candidate_loses_availability_and_cannot_be_reviewed(tmp_path, monkeypatch):
    _configure_paths(tmp_path, monkeypatch)
    monkeypatch.setattr(
        web_sources, "discover_sources", lambda query, limit=3: [_document()]
    )
    cloud_resources.discover_resources()
    candidate = cloud_resources.list_candidates()[0]
    with sqlite3.connect(db.defaults.cloud_resources_db_path) as connection:
        connection.execute(
            """UPDATE cloud_resource_candidates
               SET review_status='approved', availability_status='available',
                   reviewed_at=CURRENT_TIMESTAMP, reviewed_content_sha256=content_sha256,
                   last_seen_at=datetime('now', '-31 days')
               WHERE id=?""",
            (candidate["id"],),
        )
        connection.commit()

    assert cloud_resources.mark_stale_candidates(max_age_days=30) == 1
    stale = cloud_resources.list_candidates()[0]
    assert stale["freshness_status"] == "stale"
    assert stale["availability_status"] == "unknown"
    with pytest.raises(cloud_resources.CloudResourceConflictError, match="stale"):
        cloud_resources.review_candidate(
            stale["id"],
            expected_content_sha256=stale["content_sha256"],
            review_status="approved",
            availability_status="available",
            terms_url="https://compute.example.org/terms",
        )


def test_cloud_resource_v1_upgrade_invalidates_legacy_approval(tmp_path):
    path = tmp_path / "legacy-cloud.db"
    with sqlite3.connect(path) as connection:
        assert MigrationRunner(CLOUD_RESOURCE_MIGRATIONS[:1]).apply(connection) == 1
        connection.execute(
            """INSERT INTO cloud_resource_candidates(
                   source_url, provider_domain, title, content_sha256,
                   review_status, availability_status, quota_json, terms_url, discovery_query
               ) VALUES (?, ?, ?, ?, 'approved', 'available', ?, ?, ?)""",
            (
                "https://compute.example.org/free",
                "compute.example.org",
                "Free compute",
                "a" * 64,
                json.dumps({"cpu": "2 vCPU"}),
                "https://compute.example.org/terms",
                "free compute",
            ),
        )
        connection.commit()

    assert initialize_database_file(path, CLOUD_RESOURCE_MIGRATIONS) == 7
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            """SELECT review_status, availability_status, quota_json, terms_url,
                      freshness_status, reviewed_content_sha256
               FROM cloud_resource_candidates WHERE source_url=?""",
            ("https://compute.example.org/free",),
        ).fetchone()
    assert row == ("unreviewed", "unknown", "{}", None, "fresh", None)


def test_claim_acceptance_requires_approved_source_and_exact_current_hash(
    tmp_path, monkeypatch
):
    _configure_paths(tmp_path, monkeypatch)
    document = _document(text=(
        "Free tier includes 2 vCPU and 8 GB RAM. Read terms before use. " * 4
    ))
    monkeypatch.setattr(
        web_sources, "discover_sources", lambda query, limit=3: [document]
    )
    cloud_resources.discover_resources()
    candidate = cloud_resources.list_candidates()[0]
    claim = next(
        item for item in candidate["observed_claims"]
        if item["claim_type"] == "cpu_count"
    )

    with pytest.raises(cloud_resources.CloudResourceConflictError, match="approve the candidate"):
        cloud_resources.review_claim(
            candidate["id"],
            claim["id"],
            expected_source_content_sha256=candidate["content_sha256"],
            review_status="accepted",
        )

    cloud_resources.review_candidate(
        candidate["id"],
        expected_content_sha256=candidate["content_sha256"],
        review_status="approved",
        availability_status="unknown",
        terms_url="https://compute.example.org/terms",
    )
    reviewed = cloud_resources.review_claim(
        candidate["id"],
        claim["id"],
        expected_source_content_sha256=candidate["content_sha256"],
        review_status="accepted",
    )
    assert reviewed["review_status"] == "accepted"
    assert reviewed["reviewed_at"]
    assert reviewed["source_content_sha256"] == candidate["content_sha256"]

    with pytest.raises(cloud_resources.CloudResourceConflictError, match="evidence changed"):
        cloud_resources.review_claim(
            candidate["id"],
            claim["id"],
            expected_source_content_sha256="0" * 64,
            review_status="rejected",
        )



def test_selection_plan_history_persists_only_minimal_decision_snapshot(tmp_path, monkeypatch):
    _configure_paths(tmp_path, monkeypatch)
    plan = {
        "plan_version": "provider-neutral-selection-v1",
        "status": "candidate_found",
        "selected_candidate_id": 12,
        "execution_authorized": False,
        "remote_execution_enabled": False,
        "requirements": {
            "min_cpu_count": 2,
            "min_memory_gib": 8.0,
            "require_gpu": False,
            "gpu_models": [],
            "allowed_provider_domains": [],
        },
        "limitations": ["No remote execution."],
        "candidates": [
            {
                "candidate_id": 12,
                "provider_domain": "compute.example.org",
                "content_sha256": "a" * 64,
                "eligible_for_adapter_review": True,
                "execution_authorized": False,
                "preflight_status": "ready_for_provider_adapter",
                "capabilities": {"cpu_count": 4, "memory_gib": 16.0, "gpu_models": []},
                "reasons": [],
                "policy_blockers": [],
                "policy_review_needed": ["Provider adapter still required."],
            }
        ],
    }

    saved = cloud_resources.save_resource_selection_plan(plan)
    fetched = cloud_resources.get_resource_selection_plan(saved["plan_id"])
    listed = cloud_resources.list_resource_selection_plans(limit=10)

    assert fetched["status"] == "candidate_found"
    assert fetched["selected_candidate_id"] == 12
    assert fetched["requirements"] == plan["requirements"]
    assert fetched["candidates"][0]["content_sha256"] == "a" * 64
    assert fetched["candidates"][0]["capabilities"]["memory_gib"] == 16.0
    assert fetched["execution_authorized"] is False
    assert fetched["remote_execution_enabled"] is False
    assert listed[0]["plan_id"] == saved["plan_id"]

    with sqlite3.connect(db.defaults.cloud_resources_db_path) as connection:
        stored = connection.execute(
            "SELECT requirements_json FROM cloud_resource_selection_plans WHERE id=?",
            (saved["plan_id"],),
        ).fetchone()[0]
        columns = {
            row[1] for row in connection.execute(
                "PRAGMA table_info(cloud_resource_selection_decisions)"
            )
        }
    assert "evidence_excerpt" not in columns
    assert "private_key" not in stored


def test_selection_plan_storage_rejects_any_execution_authorization(tmp_path, monkeypatch):
    _configure_paths(tmp_path, monkeypatch)
    plan = {
        "plan_version": "provider-neutral-selection-v1",
        "status": "no_eligible_candidate",
        "selected_candidate_id": None,
        "execution_authorized": True,
        "remote_execution_enabled": False,
        "requirements": {},
        "candidates": [],
    }

    with pytest.raises(CloudResourceError, match="cannot authorize"):
        cloud_resources.save_resource_selection_plan(plan)

    with sqlite3.connect(db.defaults.cloud_resources_db_path) as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM cloud_resource_selection_plans"
        ).fetchone()[0]
    assert count == 0
