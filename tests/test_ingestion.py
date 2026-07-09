"""
Tests for the Job Ingestion Service milestone: the `JobProvider` registry,
Indeed/TotalJobs stubs, Reed's real-API-shaped provider (mocked HTTP,
never a live call), cross-source deduplication, the ingestion orchestrator,
auto-match-on-import notifications, and the closing-soon scheduled task.

No real network access anywhere in this file: NHS Jobs/Trac Jobs are
exercised via their existing local-fixture-server tests
(test_nhs_scraper.py/test_trac_scraper.py); Reed is exercised via
`httpx.MockTransport`; AI matching uses `SchedulerFakeLLMProvider` (forced
by clearing `settings.anthropic_api_key` for the duration of each test).
"""

from __future__ import annotations

from datetime import date, timedelta

import httpx
import pytest
from sqlalchemy import select

from job_automation.config.settings import settings
from job_automation.database.models import Employer, Job, JobMatch, Notification, User
from job_automation.ingestion import PROVIDER_REGISTRY, get_provider, run_ingestion
from job_automation.ingestion.auto_match_service import process_new_jobs
from job_automation.ingestion.job_provider import JobProvider, ProviderRunStats
from job_automation.ingestion.multi_location import run_per_location
from job_automation.ingestion.providers.company_career_page_provider import CompanyCareerPageProvider
from job_automation.ingestion.providers.cv_library_provider import CVLibraryProvider
from job_automation.ingestion.providers.glassdoor_provider import GlassdoorProvider
from job_automation.ingestion.providers.indeed_provider import IndeedProvider
from job_automation.ingestion.providers.reed_provider import ReedProvider, ReedProviderError
from job_automation.ingestion.providers.totaljobs_provider import TotalJobsProvider
from job_automation.profile.candidate_profile import CandidateProfile, PersonalInformation
from job_automation.profile.profile_service import ProfileService
from job_automation.scheduler.tasks import check_closing_soon_jobs, import_provider_jobs


# --- Provider registry -------------------------------------------------------


def test_provider_registry_has_all_registered_providers() -> None:
    assert set(PROVIDER_REGISTRY) == {
        "nhs_jobs",
        "trac_jobs",
        "reed",
        "indeed",
        "totaljobs",
        "glassdoor",
        "cv_library",
        "company_career_pages",
    }


def test_disabled_placeholder_providers_are_excluded_from_default_ingestion_list() -> None:
    """Placeholder providers are discoverable/testable via the registry, but
    must never be part of the default provider list — running them would
    only ever produce a guaranteed failure."""
    disabled = {"indeed", "totaljobs", "glassdoor", "cv_library", "company_career_pages"}
    assert disabled.isdisjoint(settings.job_ingestion_providers)


def test_get_provider_raises_for_unknown_source() -> None:
    with pytest.raises(KeyError):
        get_provider("not_a_real_source")


# --- Multi-location helper ----------------------------------------------------


def test_run_per_location_calls_once_per_location_and_aggregates_stats() -> None:
    seen_locations: list[str | None] = []

    def _run_one(location: str | None) -> ProviderRunStats:
        seen_locations.append(location)
        return ProviderRunStats(source="test", jobs_seen=2, jobs_created=1, jobs_updated=1)

    result = run_per_location("test", ["London", "Manchester", "Birmingham"], _run_one)

    assert seen_locations == ["London", "Manchester", "Birmingham"], "one call per location, not one joined string"
    assert result.jobs_seen == 6
    assert result.jobs_created == 3
    assert result.jobs_updated == 3


def test_run_per_location_runs_once_with_none_when_no_locations_configured() -> None:
    seen_locations: list[str | None] = []

    def _run_one(location: str | None) -> ProviderRunStats:
        seen_locations.append(location)
        return ProviderRunStats(source="test", jobs_seen=1)

    result = run_per_location("test", [], _run_one)

    assert seen_locations == [None]
    assert result.jobs_seen == 1


def test_run_per_location_empty_results_produce_zeroed_stats_not_an_error() -> None:
    def _run_one(location: str | None) -> ProviderRunStats:
        return ProviderRunStats(source="test")  # a real search that simply found nothing

    result = run_per_location("test", ["London"], _run_one)

    assert result.jobs_seen == 0
    assert result.jobs_created == 0
    assert result.newly_created_job_ids == []


def test_run_per_location_one_location_failing_does_not_stop_the_others() -> None:
    def _run_one(location: str | None) -> ProviderRunStats:
        if location == "Manchester":
            raise RuntimeError("simulated search failure for Manchester")
        return ProviderRunStats(source="test", jobs_seen=1, jobs_created=1)

    result = run_per_location("test", ["London", "Manchester", "Birmingham"], _run_one)

    # London + Birmingham succeeded; Manchester's failure was logged and skipped.
    assert result.jobs_seen == 2
    assert result.jobs_created == 2


def test_nhs_provider_searches_each_configured_location_separately(
    db_session, nhs_fixture_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With N configured locations, NHSProvider must run N separate
    searches (one per location) rather than joining them into one query
    string — verified by pointing two locations at the same fixture page
    and confirming jobs_seen is double a single-location run's count, i.e.
    the search genuinely ran twice."""
    from job_automation.ingestion.providers.nhs_provider import NHSProvider

    monkeypatch.setattr(settings, "scrape_keywords", ["Registered Nurse"])
    monkeypatch.setattr(settings, "scrape_locations", ["London"])
    single = NHSProvider(base_url=nhs_fixture_url, search_path="/search_page_1.html").fetch_jobs(db_session)
    db_session.commit()

    monkeypatch.setattr(settings, "scrape_locations", ["London", "Manchester"])
    doubled = NHSProvider(base_url=nhs_fixture_url, search_path="/search_page_1.html").fetch_jobs(db_session)
    db_session.commit()

    assert doubled.jobs_seen == single.jobs_seen * 2


def test_trac_provider_uses_configured_base_url_when_registry_constructed(monkeypatch: pytest.MonkeyPatch) -> None:
    """`get_provider("trac_jobs")` (the path the scheduler/manual-import
    button actually use) constructs `TracProvider()` with no arguments —
    real ingestion only ever reaches a real trust's site if one is
    configured via `settings.trac_jobs_base_url`, since Trac Jobs has no
    single national search the way NHS Jobs does."""
    monkeypatch.setattr(settings, "trac_jobs_base_url", "https://a-real-trust.trac.jobs")
    provider = get_provider("trac_jobs")
    assert provider._base_url == "https://a-real-trust.trac.jobs"


def test_trac_provider_explicit_base_url_overrides_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "trac_jobs_base_url", "https://from-settings.trac.jobs")
    from job_automation.ingestion.providers.trac_provider import TracProvider

    provider = TracProvider(base_url="https://explicit-override.trac.jobs")
    assert provider._base_url == "https://explicit-override.trac.jobs"


# --- Disabled placeholder providers -------------------------------------------


def test_indeed_provider_raises_not_implemented_with_clear_message(db_session) -> None:
    provider = IndeedProvider()
    with pytest.raises(NotImplementedError, match="compliant data source"):
        provider.fetch_jobs(db_session)


def test_totaljobs_provider_raises_not_implemented_with_clear_message(db_session) -> None:
    provider = TotalJobsProvider()
    with pytest.raises(NotImplementedError, match="compliant data source"):
        provider.fetch_jobs(db_session)


def test_glassdoor_provider_raises_not_implemented_with_clear_message(db_session) -> None:
    provider = GlassdoorProvider()
    with pytest.raises(NotImplementedError, match="compliant data source"):
        provider.fetch_jobs(db_session)


def test_cv_library_provider_raises_not_implemented_with_clear_message(db_session) -> None:
    provider = CVLibraryProvider()
    with pytest.raises(NotImplementedError, match="partner program"):
        provider.fetch_jobs(db_session)


def test_company_career_page_provider_raises_not_implemented_with_clear_message(db_session) -> None:
    provider = CompanyCareerPageProvider()
    with pytest.raises(NotImplementedError, match="dedicated JobProvider subclass"):
        provider.fetch_jobs(db_session)


# --- Reed provider (mocked HTTP, never live) ---------------------------------


def test_reed_provider_requires_an_api_key(db_session) -> None:
    provider = ReedProvider(api_key=None)
    with pytest.raises(ReedProviderError, match="requires an API key"):
        provider.fetch_jobs(db_session)


def _reed_mock_client(jobs: list[dict]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": jobs, "totalResults": len(jobs)})

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_reed_provider_normalizes_and_persists_jobs(db_session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "scrape_keywords", ["Staff Nurse"])
    monkeypatch.setattr(settings, "scrape_locations", ["London"])

    client = _reed_mock_client(
        [
            {
                "jobId": 555,
                "employerName": "Riverside NHS Foundation Trust",
                "jobTitle": "Staff Nurse",
                "locationName": "London",
                "minimumSalary": 29000,
                "maximumSalary": 35000,
                "currency": "GBP",
                "jobUrl": "https://www.reed.co.uk/jobs/staff-nurse/555",
                "jobDescription": "Provide excellent nursing care.",
                "date": "01/07/2026",
                "expirationDate": "01/08/2026",
                "fullTime": True,
                "permanent": True,
            }
        ]
    )
    provider = ReedProvider(api_key="fake-key", http_client=client)
    stats = provider.fetch_jobs(db_session)
    db_session.commit()

    assert stats.jobs_created == 1
    assert stats.jobs_failed == 0
    job = db_session.scalars(select(Job)).first()
    assert job.title == "Staff Nurse"
    assert job.source_site == "reed"
    assert job.external_id == "555"
    assert job.salary_min == 29000
    assert job.salary_max == 35000
    assert job.contract_type == "Permanent"
    assert job.working_pattern == "Full-time"
    assert job.closing_date == date(2026, 8, 1)


def test_reed_provider_request_failure_does_not_abort_whole_run(db_session, monkeypatch: pytest.MonkeyPatch) -> None:
    """A single (keyword, location) request failing is logged and skipped,
    not raised out of fetch_jobs() — one bad search shouldn't lose every
    other combination's results. A missing API key (a configuration
    problem, not a per-request one) still raises immediately instead;
    see test_reed_provider_requires_an_api_key."""
    monkeypatch.setattr(settings, "scrape_keywords", ["Staff Nurse"])
    monkeypatch.setattr(settings, "scrape_locations", ["London"])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "server error"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = ReedProvider(api_key="fake-key", http_client=client)
    stats = provider.fetch_jobs(db_session)

    assert stats.jobs_seen == 0
    assert stats.jobs_created == 0


# --- Cross-source deduplication -----------------------------------------------


def test_cross_source_duplicate_updates_existing_row_instead_of_creating_one(
    db_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same real-world role, first seen via Reed then re-posted on a
    different source with a different external ID, must resolve to one Job
    row (title+employer+location dedup), per this milestone's explicit
    "OR title + employer + location" requirement."""
    monkeypatch.setattr(settings, "scrape_keywords", ["Staff Nurse"])
    monkeypatch.setattr(settings, "scrape_locations", ["London"])

    reed_client = _reed_mock_client(
        [
            {
                "jobId": 111,
                "employerName": "Riverside NHS Foundation Trust",
                "jobTitle": "Staff Nurse",
                "locationName": "London",
                "jobUrl": "https://www.reed.co.uk/jobs/staff-nurse/111",
                "jobDescription": "Reed listing.",
            }
        ]
    )
    ReedProvider(api_key="fake-key", http_client=reed_client).fetch_jobs(db_session)
    db_session.commit()
    assert db_session.scalar(select(Job).limit(1)) is not None
    assert len(list(db_session.scalars(select(Job)))) == 1

    from job_automation.database.services.job_ingestion_service import JobIngestionService
    from job_automation.scrapers.base.base_parser import ParsedJob

    duplicate = ParsedJob(
        title="Staff Nurse",
        employer="Riverside NHS Foundation Trust",
        location="London",
        job_url="https://trac.jobs/vacancy/999",
        reference_number="TRAC-999",
    )
    result = JobIngestionService(db_session, source_site="trac_jobs").save_parsed_job(duplicate)
    db_session.commit()

    assert result.created is False
    assert len(list(db_session.scalars(select(Job)))) == 1
    assert result.job.source_site == "reed", "the first source's identity is preserved, not overwritten"


# --- Ingestion orchestrator ---------------------------------------------------


class _FakeProvider(JobProvider):
    source_name = "fake_source"

    def fetch_jobs(self, session) -> ProviderRunStats:
        from job_automation.database.services.job_ingestion_service import JobIngestionService
        from job_automation.scrapers.base.base_parser import ParsedJob

        ingestion = JobIngestionService(session, source_site=self.source_name)
        ingestion.save_parsed_job(
            ParsedJob(title="Fake Job", employer="Fake Employer", job_url="https://fake/1", reference_number="F1")
        )
        return ProviderRunStats(
            source=self.source_name, jobs_seen=1, jobs_created=1, newly_created_job_ids=list(ingestion.created_job_ids)
        )


def test_run_ingestion_aggregates_stats_and_isolates_provider_failures(db_session) -> None:
    PROVIDER_REGISTRY["fake_source"] = _FakeProvider
    try:
        result = run_ingestion(db_session, providers=["fake_source", "indeed", "totaljobs"])
    finally:
        del PROVIDER_REGISTRY["fake_source"]

    assert result.jobs_created == 1
    assert result.jobs_seen == 1
    assert set(result.provider_errors) == {"indeed", "totaljobs"}
    assert "fake_source" in result.provider_stats
    assert len(result.newly_created_job_ids) == 1


# --- Auto-match-on-import notifications ---------------------------------------


def _seed_user_with_profile(db_session) -> User:
    user = User(email="jane@example.com", full_name="Jane Doe", hashed_password="unused-in-these-tests")
    db_session.add(user)
    db_session.flush()
    ProfileService(db_session).save(
        CandidateProfile(personal_information=PersonalInformation(full_name="Jane Doe")), user_id=user.id
    )
    return user


def test_process_new_jobs_notifies_band3_and_sponsorship_regardless_of_match_score(
    db_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "anthropic_api_key", None)  # force SchedulerFakeLLMProvider, no real call
    user = _seed_user_with_profile(db_session)
    employer = Employer(name="Riverside NHS Trust")
    db_session.add(employer)
    db_session.flush()
    job_band3 = Job(employer=employer, title="HCA", source_site="nhs_jobs", external_id="J1", url="https://x/1", band="Band 3")
    job_sponsor = Job(employer=employer, title="Nurse", source_site="nhs_jobs", external_id="J2", url="https://x/2", visa_sponsorship=True)
    db_session.add_all([job_band3, job_sponsor])
    db_session.commit()

    summary = process_new_jobs(db_session, [job_band3.id, job_sponsor.id])
    db_session.commit()

    assert summary["band3_notifications"] == 1
    assert summary["sponsorship_notifications"] == 1
    types = {n.type for n in db_session.scalars(select(Notification).where(Notification.user_id == user.id))}
    assert "new_band3_job" in types
    assert "new_sponsorship_job" in types


def test_process_new_jobs_notifies_high_match_above_threshold(db_session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "anthropic_api_key", None)
    monkeypatch.setattr(settings, "job_ingestion_high_match_threshold", 1.0)  # SchedulerFakeLLMProvider's score always clears this
    user = _seed_user_with_profile(db_session)
    employer = Employer(name="Riverside NHS Trust")
    db_session.add(employer)
    db_session.flush()
    job = Job(employer=employer, title="Staff Nurse", source_site="nhs_jobs", external_id="J1", url="https://x/1")
    db_session.add(job)
    db_session.commit()

    summary = process_new_jobs(db_session, [job.id])
    db_session.commit()

    assert summary["high_match_notifications"] == 1
    notification = db_session.scalars(
        select(Notification).where(Notification.user_id == user.id, Notification.type == "new_high_match_job")
    ).first()
    assert notification is not None
    assert "generate a cover letter" in notification.message


def test_process_new_jobs_does_not_notify_below_threshold(db_session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "anthropic_api_key", None)
    monkeypatch.setattr(settings, "job_ingestion_high_match_threshold", 99.0)  # SchedulerFakeLLMProvider never clears this
    _seed_user_with_profile(db_session)
    employer = Employer(name="Riverside NHS Trust")
    db_session.add(employer)
    db_session.flush()
    job = Job(employer=employer, title="Staff Nurse", source_site="nhs_jobs", external_id="J1", url="https://x/1")
    db_session.add(job)
    db_session.commit()

    summary = process_new_jobs(db_session, [job.id])
    db_session.commit()

    assert summary["high_match_notifications"] == 0


def test_process_new_jobs_is_a_no_op_for_an_empty_job_list(db_session) -> None:
    summary = process_new_jobs(db_session, [])
    assert summary == {"high_match_notifications": 0, "band3_notifications": 0, "sponsorship_notifications": 0}


# --- import_provider_jobs scheduled task --------------------------------------


def test_import_provider_jobs_task_runs_configured_providers_and_publishes_job_imported(
    db_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "anthropic_api_key", None)
    PROVIDER_REGISTRY["fake_source"] = _FakeProvider
    monkeypatch.setattr(settings, "job_ingestion_providers", ["fake_source"])
    try:
        summary = import_provider_jobs.run(db_session)
    finally:
        del PROVIDER_REGISTRY["fake_source"]

    assert summary["jobs_created"] == 1
    assert summary["providers_run"] == ["fake_source"]
    notification = db_session.scalars(select(Notification).where(Notification.type == "job_imported")).first()
    assert notification is not None


# --- check_closing_soon_jobs scheduled task ------------------------------------


def test_check_closing_soon_jobs_notifies_matched_users_once(db_session) -> None:
    user = User(email="jane@example.com", full_name="Jane Doe", hashed_password="unused-in-these-tests")
    employer = Employer(name="Riverside NHS Trust")
    db_session.add_all([user, employer])
    db_session.flush()
    job_soon = Job(
        employer=employer, title="Staff Nurse", source_site="nhs_jobs", external_id="J1", url="https://x/1",
        closing_date=date.today() + timedelta(days=1),
    )
    job_far = Job(
        employer=employer, title="HCA", source_site="nhs_jobs", external_id="J2", url="https://x/2",
        closing_date=date.today() + timedelta(days=30),
    )
    db_session.add_all([job_soon, job_far])
    db_session.flush()
    db_session.add(JobMatch(job=job_soon, user=user, match_score=70.0, analysis={}))
    db_session.commit()

    result = check_closing_soon_jobs.run(db_session)
    db_session.commit()

    assert result == {"jobs_checked": 1, "jobs_notified": 1, "notifications_sent": 1}
    assert job_soon.closing_soon_notified_at is not None
    assert job_far.closing_soon_notified_at is None

    # Re-running must not re-notify.
    second_result = check_closing_soon_jobs.run(db_session)
    assert second_result == {"jobs_checked": 0, "jobs_notified": 0, "notifications_sent": 0}


def test_check_closing_soon_jobs_skips_jobs_with_no_matched_users(db_session) -> None:
    employer = Employer(name="Riverside NHS Trust")
    db_session.add(employer)
    db_session.flush()
    job = Job(
        employer=employer, title="Staff Nurse", source_site="nhs_jobs", external_id="J1", url="https://x/1",
        closing_date=date.today(),
    )
    db_session.add(job)
    db_session.commit()

    result = check_closing_soon_jobs.run(db_session)
    db_session.commit()

    assert result["jobs_notified"] == 1  # still marked notified, just with zero notifications sent
    assert result["notifications_sent"] == 0
