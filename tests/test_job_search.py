"""
Tests for the Job Management milestone's additive extension to
`JobFilter`/`JobRepository.search()`: `max_salary`, `remote`, `closing_soon`,
`expired`, `keywords`, and the `user_id`-scoped saved/favourite/archived/
pipeline-stage filters (backed by a `LEFT OUTER JOIN` onto `SavedJob`).

Every pre-existing filter (search, location, min_salary, band,
employer_name, visa_sponsorship, employment_type, sort_by) already has
coverage from the web dashboard milestone elsewhere; this file only covers
what's new, plus one regression check that the old behaviour (no
`user_id`) is untouched by the join logic.
"""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy.orm import Session

from job_automation.database.models.employer import Employer
from job_automation.database.models.job import Job
from job_automation.database.models.user import User
from job_automation.database.repositories.job_repository import JobFilter, JobRepository
from job_automation.job_organization.job_organization_service import JobOrganizationService
from job_automation.job_organization.job_organization_models import PipelineStage


def _make_job(employer: Employer, **overrides) -> Job:
    fields = dict(
        employer=employer,
        title="Healthcare Assistant",
        location="London",
        source_site="nhs_jobs",
        external_id=overrides.pop("external_id", "REF-1"),
        url=overrides.pop("url", "https://example.com/job/1"),
        is_active=True,
        salary_min=22000,
        salary_max=25000,
    )
    fields.update(overrides)
    return Job(**fields)


def test_search_max_salary_excludes_jobs_priced_above_range(db_session: Session) -> None:
    employer = Employer(name="Example NHS Trust")
    cheap = _make_job(employer, external_id="REF-1", url="https://example.com/1", salary_min=20000, salary_max=22000)
    expensive = _make_job(
        employer, external_id="REF-2", url="https://example.com/2", salary_min=50000, salary_max=60000
    )
    db_session.add_all([employer, cheap, expensive])
    db_session.commit()

    results = JobRepository(db_session).search(JobFilter(max_salary=25000))

    assert cheap in results
    assert expensive not in results


def test_search_remote_true_matches_remote_working_pattern(db_session: Session) -> None:
    employer = Employer(name="Example NHS Trust")
    remote_job = _make_job(
        employer, external_id="REF-R", url="https://example.com/r", working_pattern="Full time, Remote"
    )
    onsite_job = _make_job(
        employer, external_id="REF-O", url="https://example.com/o", working_pattern="Full time, On-site"
    )
    db_session.add_all([employer, remote_job, onsite_job])
    db_session.commit()

    remote_results = JobRepository(db_session).search(JobFilter(remote=True))
    onsite_results = JobRepository(db_session).search(JobFilter(remote=False))

    assert remote_job in remote_results and onsite_job not in remote_results
    assert onsite_job in onsite_results and remote_job not in onsite_results


def test_search_closing_soon_excludes_jobs_closing_far_in_future(db_session: Session) -> None:
    employer = Employer(name="Example NHS Trust")
    soon = _make_job(
        employer,
        external_id="REF-SOON",
        url="https://example.com/soon",
        closing_date=date.today() + timedelta(days=3),
    )
    later = _make_job(
        employer,
        external_id="REF-LATER",
        url="https://example.com/later",
        closing_date=date.today() + timedelta(days=30),
    )
    db_session.add_all([employer, soon, later])
    db_session.commit()

    results = JobRepository(db_session).search(JobFilter(closing_soon=True))

    assert soon in results
    assert later not in results


def test_search_expired_matches_past_closing_date_or_inactive(db_session: Session) -> None:
    employer = Employer(name="Example NHS Trust")
    expired_by_date = _make_job(
        employer,
        external_id="REF-EXP1",
        url="https://example.com/exp1",
        closing_date=date.today() - timedelta(days=1),
    )
    expired_by_flag = _make_job(
        employer, external_id="REF-EXP2", url="https://example.com/exp2", is_active=False
    )
    active_job = _make_job(
        employer,
        external_id="REF-ACT",
        url="https://example.com/act",
        closing_date=date.today() + timedelta(days=10),
    )
    db_session.add_all([employer, expired_by_date, expired_by_flag, active_job])
    db_session.commit()

    results = JobRepository(db_session).search(JobFilter(expired=True))

    assert expired_by_date in results
    assert expired_by_flag in results
    assert active_job not in results


def test_search_keywords_matches_title_like_search_field(db_session: Session) -> None:
    employer = Employer(name="Example NHS Trust")
    matching = _make_job(
        employer, external_id="REF-K1", url="https://example.com/k1", title="Senior Care Assistant"
    )
    other = _make_job(employer, external_id="REF-K2", url="https://example.com/k2", title="Porter")
    db_session.add_all([employer, matching, other])
    db_session.commit()

    results = JobRepository(db_session).search(JobFilter(keywords="Care"))

    assert matching in results
    assert other not in results


def test_search_sort_by_employer_and_band(db_session: Session) -> None:
    employer_a = Employer(name="Alpha Trust")
    employer_z = Employer(name="Zulu Trust")
    job_a = _make_job(employer_a, external_id="REF-A", url="https://example.com/a", band="Band 5")
    job_z = _make_job(employer_z, external_id="REF-Z", url="https://example.com/z", band="Band 7")
    db_session.add_all([employer_a, employer_z, job_a, job_z])
    db_session.commit()

    by_employer = JobRepository(db_session).search(JobFilter(sort_by="employer", sort_descending=False))
    assert [j.id for j in by_employer] == [job_a.id, job_z.id]

    by_band = JobRepository(db_session).search(JobFilter(sort_by="band", sort_descending=False))
    assert [j.id for j in by_band] == [job_a.id, job_z.id]


def test_search_user_scoped_excludes_hidden_and_archived_by_default(db_session: Session) -> None:
    user = User(email="candidate@example.com", full_name="Test Candidate", hashed_password="unused")
    employer = Employer(name="Example NHS Trust")
    visible = _make_job(employer, external_id="REF-V", url="https://example.com/v")
    hidden = _make_job(employer, external_id="REF-H", url="https://example.com/h")
    archived = _make_job(employer, external_id="REF-AR", url="https://example.com/ar")
    db_session.add_all([user, employer, visible, hidden, archived])
    db_session.commit()

    org = JobOrganizationService(db_session)
    org.hide(user_id=user.id, job_id=hidden.id)
    org.archive(user_id=user.id, job_id=archived.id)
    db_session.commit()

    results = JobRepository(db_session).search(JobFilter(user_id=user.id))

    assert visible in results
    assert hidden not in results
    assert archived not in results


def test_search_archived_only_shows_exactly_the_restore_view(db_session: Session) -> None:
    user = User(email="candidate2@example.com", full_name="Test Candidate 2", hashed_password="unused")
    employer = Employer(name="Example NHS Trust")
    visible = _make_job(employer, external_id="REF-V2", url="https://example.com/v2")
    archived = _make_job(employer, external_id="REF-AR2", url="https://example.com/ar2")
    db_session.add_all([user, employer, visible, archived])
    db_session.commit()

    org = JobOrganizationService(db_session)
    org.archive(user_id=user.id, job_id=archived.id)
    db_session.commit()

    results = JobRepository(db_session).search(JobFilter(user_id=user.id, archived_only=True))

    assert archived in results
    assert visible not in results


def test_search_favourite_only_and_pipeline_stage_filters(db_session: Session) -> None:
    user = User(email="candidate3@example.com", full_name="Test Candidate 3", hashed_password="unused")
    employer = Employer(name="Example NHS Trust")
    favourite_job = _make_job(employer, external_id="REF-FAV", url="https://example.com/fav")
    plain_job = _make_job(employer, external_id="REF-PLAIN", url="https://example.com/plain")
    db_session.add_all([user, employer, favourite_job, plain_job])
    db_session.commit()

    org = JobOrganizationService(db_session)
    org.favourite(user_id=user.id, job_id=favourite_job.id)
    org.save(user_id=user.id, job_id=plain_job.id)
    org.update_stage(user_id=user.id, job_id=plain_job.id, target_stage=PipelineStage.INTERESTED)
    db_session.commit()

    favourite_results = JobRepository(db_session).search(JobFilter(user_id=user.id, favourite_only=True))
    assert favourite_results == [favourite_job]

    interested_results = JobRepository(db_session).search(
        JobFilter(user_id=user.id, pipeline_stage=PipelineStage.INTERESTED.value)
    )
    assert interested_results == [plain_job]


def test_search_without_user_id_is_unaffected_by_saved_job_rows(db_session: Session) -> None:
    """Regression check: adding the user-scoped join must not change
    results for the pre-existing, unscoped (`user_id=None`) call shape."""
    user = User(email="candidate4@example.com", full_name="Test Candidate 4", hashed_password="unused")
    employer = Employer(name="Example NHS Trust")
    job = _make_job(employer, external_id="REF-UNSCOPED", url="https://example.com/unscoped")
    db_session.add_all([user, employer, job])
    db_session.commit()

    org = JobOrganizationService(db_session)
    org.hide(user_id=user.id, job_id=job.id)
    db_session.commit()

    results = JobRepository(db_session).search(JobFilter())

    assert job in results
