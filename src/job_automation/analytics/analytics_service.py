"""
Computes the aggregate statistics the dashboard/analytics pages display.

This is genuinely new backend logic — no analytics capability existed
anywhere in this codebase before this milestone, so there was nothing to
"reuse" here the way `web/` reuses `MatchingService`/`DocumentService`/
`WorkflowService`/`ProfileService`. Kept as its own package (sibling to
`ai`/`profile`/`documents`/`workflows`), not inside `web/`, so the
computation lives in the backend and the web layer only calls it and
renders — consistent with "do not duplicate business logic inside the
frontend."

Queries models directly (there's no `AnalyticsRepository` to go through —
several of the tables queried here, e.g. `WorkflowStatusHistoryRecord`,
`WorkflowAuditLogRecord`, don't have a dedicated repository at all), the
same way existing repositories themselves query models directly.
"""

from __future__ import annotations

import uuid
from collections import Counter
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from job_automation.analytics.analytics_models import (
    ActivityItem,
    AIStatus,
    AnalyticsReport,
    DashboardSummary,
    EmployerInterviewStats,
    EmployerOutcomeSummary,
    InterviewAnalyticsSummary,
    JobIngestionSummary,
    JobMarketAnalytics,
    JobOrganizationSummary,
    LatestJobSummary,
    MonthlyCount,
    NamedCount,
    PipelineStageCount,
    SalaryBucketCount,
    ScoreBucket,
    UpcomingDeadline,
)
from job_automation.config.settings import settings
from job_automation.database.models.application_workflow_record import ApplicationWorkflowRecord
from job_automation.database.models.employer import Employer
from job_automation.database.models.generated_document_record import GeneratedDocumentRecord
from job_automation.database.models.interview_record import InterviewRecord
from job_automation.database.models.job import Job
from job_automation.database.models.job_match import JobMatch
from job_automation.database.models.job_reminder import JobReminder
from job_automation.database.models.saved_job import SavedJob
from job_automation.database.models.workflow_audit_log_record import WorkflowAuditLogRecord
from job_automation.database.models.workflow_status_history_record import (
    WorkflowStatusHistoryRecord,
)
from job_automation.interviews.interview_models import InterviewStatus
from job_automation.job_organization.job_organization_models import PipelineStage
from job_automation.utils.helpers import utc_now

_INTERVIEW_UPCOMING_STATUSES = frozenset(
    {InterviewStatus.SCHEDULED.value, InterviewStatus.UPCOMING.value, InterviewStatus.RESCHEDULED.value}
)
_INTERVIEW_COMPLETED_STATUSES = frozenset(
    {
        InterviewStatus.COMPLETED.value,
        InterviewStatus.OFFER_RECEIVED.value,
        InterviewStatus.REJECTED.value,
        InterviewStatus.WAITING_DECISION.value,
    }
)
_INTERVIEW_CANCELLED_STATUSES = frozenset({InterviewStatus.CANCELLED.value, InterviewStatus.MISSED.value})

_SCORE_BUCKETS = ("0-20", "20-40", "40-60", "60-80", "80-100")

# A saved job counts towards "Applications" once it's reached "Applied" or
# further — "New"/"Interested"/"Documents Ready" are pre-application states.
_APPLIED_OR_LATER_STAGES = frozenset(
    {
        PipelineStage.APPLIED.value,
        PipelineStage.INTERVIEW.value,
        PipelineStage.OFFER.value,
        PipelineStage.REJECTED.value,
    }
)


class AnalyticsService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def dashboard_summary(self, user_id: uuid.UUID) -> DashboardSummary:
        return DashboardSummary(
            jobs_discovered=self._count(select(func.count()).select_from(Job)),
            jobs_matched=self._count(
                select(func.count()).select_from(JobMatch).where(JobMatch.user_id == user_id)
            ),
            applications=self._count(
                select(func.count())
                .select_from(ApplicationWorkflowRecord)
                .where(ApplicationWorkflowRecord.user_id == user_id)
            ),
            pending_review=self._count(
                select(func.count())
                .select_from(GeneratedDocumentRecord)
                .where(
                    GeneratedDocumentRecord.user_id == user_id,
                    GeneratedDocumentRecord.status.in_(["draft", "needs_review"]),
                )
            ),
            approved_documents=self._count(
                select(func.count())
                .select_from(GeneratedDocumentRecord)
                .where(GeneratedDocumentRecord.user_id == user_id, GeneratedDocumentRecord.status == "approved")
            ),
            rejected_documents=self._count(
                select(func.count())
                .select_from(GeneratedDocumentRecord)
                .where(GeneratedDocumentRecord.user_id == user_id, GeneratedDocumentRecord.status == "rejected")
            ),
            interview_invitations=self._count_status_history_events(user_id, "interview"),
            offers=self._count_status_history_events(user_id, "offer"),
        )

    def recent_activity(self, user_id: uuid.UUID, *, limit: int = 10) -> list[ActivityItem]:
        rows = self._session.execute(
            select(WorkflowAuditLogRecord, Job.title, Employer.name)
            .join(ApplicationWorkflowRecord, ApplicationWorkflowRecord.id == WorkflowAuditLogRecord.workflow_id)
            .join(Job, Job.id == ApplicationWorkflowRecord.job_id)
            .join(Employer, Employer.id == Job.employer_id)
            .where(ApplicationWorkflowRecord.user_id == user_id)
            .order_by(WorkflowAuditLogRecord.created_at.desc())
            .limit(limit)
        ).all()
        return [
            ActivityItem(
                action=entry.action,
                details=entry.details or {},
                occurred_at=entry.created_at,
                job_title=job_title,
                employer_name=employer_name,
            )
            for entry, job_title, employer_name in rows
        ]

    def upcoming_deadlines(self, user_id: uuid.UUID, *, limit: int = 10) -> list[UpcomingDeadline]:
        today = date.today()
        rows = self._session.execute(
            select(Job.id, Job.title, Employer.name, Job.closing_date)
            .join(Employer, Employer.id == Job.employer_id)
            .join(JobMatch, JobMatch.job_id == Job.id)
            .where(JobMatch.user_id == user_id, Job.closing_date.is_not(None), Job.closing_date >= today)
            .distinct()
            .order_by(Job.closing_date.asc())
            .limit(limit)
        ).all()
        return [
            UpcomingDeadline(job_id=str(job_id), job_title=title, employer_name=employer_name, closing_date=closing)
            for job_id, title, employer_name, closing in rows
        ]

    def build_report(self, user_id: uuid.UUID) -> AnalyticsReport:
        return AnalyticsReport(
            applications_per_month=self._monthly_counts(ApplicationWorkflowRecord, user_id),
            match_score_distribution=self._match_score_distribution(user_id),
            interview_rate=self._workflow_stage_rate(user_id, "interview"),
            offer_rate=self._workflow_stage_rate(user_id, "offer"),
            approval_rate=self._document_decision_rate(user_id, "approved"),
            rejection_rate=self._document_decision_rate(user_id, "rejected"),
            documents_generated_per_month=self._monthly_counts(GeneratedDocumentRecord, user_id),
            top_employers=self._top_employers(user_id),
            top_requested_skills=self._top_requested_skills(user_id),
        )

    def job_organization_summary(self, user_id: uuid.UUID) -> JobOrganizationSummary:
        """Stats for the Kanban-board side of the dashboard — built from
        `SavedJob`/`JobReminder`, never from `JobMatch`/
        `ApplicationWorkflowRecord` (see the module docstring on
        `JobOrganizationSummary` for why those stay separate)."""
        stage_counts = self._pipeline_stage_counts(user_id)
        stage_lookup = {row.stage: row.count for row in stage_counts}
        today = date.today()
        return JobOrganizationSummary(
            jobs_saved=self._count(
                select(func.count())
                .select_from(SavedJob)
                .where(SavedJob.user_id == user_id, SavedJob.is_saved.is_(True))
            ),
            jobs_favourited=self._count(
                select(func.count())
                .select_from(SavedJob)
                .where(SavedJob.user_id == user_id, SavedJob.is_favourite.is_(True))
            ),
            jobs_archived=self._count(
                select(func.count())
                .select_from(SavedJob)
                .where(SavedJob.user_id == user_id, SavedJob.is_archived.is_(True))
            ),
            applications=sum(stage_lookup.get(stage, 0) for stage in _APPLIED_OR_LATER_STAGES),
            interviews=stage_lookup.get(PipelineStage.INTERVIEW.value, 0),
            offers=stage_lookup.get(PipelineStage.OFFER.value, 0),
            rejected=stage_lookup.get(PipelineStage.REJECTED.value, 0),
            upcoming_deadlines=self._count(
                select(func.count())
                .select_from(SavedJob)
                .where(SavedJob.user_id == user_id, SavedJob.deadline.is_not(None), SavedJob.deadline >= today)
            ),
            upcoming_reminders=self._count(
                select(func.count())
                .select_from(JobReminder)
                .join(SavedJob, SavedJob.id == JobReminder.saved_job_id)
                .where(SavedJob.user_id == user_id, JobReminder.is_sent.is_(False), JobReminder.remind_at >= utc_now())
            ),
            stage_counts=stage_counts,
            favourite_employers=self._favourite_employers(user_id),
        )

    def list_employer_outcome_summaries(self, user_id: uuid.UUID) -> tuple[EmployerOutcomeSummary, ...]:
        """Per-employer application/interview/offer/rejection counts for
        the CRM's success-rate analytics.

        `applications_sent`/`interviews`/`offers` come from
        `WorkflowStatusHistoryRecord` (the same authoritative source
        `dashboard_summary()`/`build_report()` already use for these
        counts account-wide — this just adds the employer grouping).
        `rejections` comes from `SavedJob.pipeline_stage ==
        PipelineStage.REJECTED` instead: `WorkflowStatus.REJECTED` means "a
        reviewer rejected the drafted document" (non-terminal, loops back
        to drafting), not "the employer rejected the candidate" — using it
        here would silently miscount document-review churn as employer
        rejections. `PipelineStage.REJECTED` is the one place this schema
        actually records an employer's rejection (see
        `job_organization_models.py`'s docstring).

        Only employers with at least one signal (an application, an
        interview, an offer, or a pipeline rejection) are included —
        an employer with zero interaction isn't a CRM outcome yet."""
        stage_rows = self._session.execute(
            select(Employer.id, Employer.name, WorkflowStatusHistoryRecord.to_status, func.count())
            .select_from(WorkflowStatusHistoryRecord)
            .join(
                ApplicationWorkflowRecord,
                ApplicationWorkflowRecord.id == WorkflowStatusHistoryRecord.workflow_id,
            )
            .join(Job, Job.id == ApplicationWorkflowRecord.job_id)
            .join(Employer, Employer.id == Job.employer_id)
            .where(
                ApplicationWorkflowRecord.user_id == user_id,
                WorkflowStatusHistoryRecord.to_status.in_(["applied", "interview", "offer"]),
            )
            .group_by(Employer.id, Employer.name, WorkflowStatusHistoryRecord.to_status)
        ).all()

        rejection_rows = self._session.execute(
            select(Employer.id, Employer.name, func.count())
            .select_from(SavedJob)
            .join(Job, Job.id == SavedJob.job_id)
            .join(Employer, Employer.id == Job.employer_id)
            .where(SavedJob.user_id == user_id, SavedJob.pipeline_stage == PipelineStage.REJECTED.value)
            .group_by(Employer.id, Employer.name)
        ).all()

        by_employer: dict[uuid.UUID, dict] = {}
        for employer_id, employer_name, to_status, count in stage_rows:
            entry = by_employer.setdefault(
                employer_id, {"name": employer_name, "applied": 0, "interview": 0, "offer": 0, "rejected": 0}
            )
            entry[to_status] = count
        for employer_id, employer_name, count in rejection_rows:
            entry = by_employer.setdefault(
                employer_id, {"name": employer_name, "applied": 0, "interview": 0, "offer": 0, "rejected": 0}
            )
            entry["rejected"] = count

        summaries = [
            self._build_employer_outcome_summary(employer_id, values) for employer_id, values in by_employer.items()
        ]
        return tuple(sorted(summaries, key=lambda s: s.applications_sent, reverse=True))

    def employer_outcome_summary(self, user_id: uuid.UUID, employer_id: uuid.UUID) -> EmployerOutcomeSummary:
        """The single-employer view for an employer's profile page —
        shares the exact same computation as `list_employer_outcome_summaries()`
        (one source of truth), just filtered to one employer, with an
        all-zero result for an employer that has no signal yet."""
        for summary in self.list_employer_outcome_summaries(user_id):
            if summary.employer_id == str(employer_id):
                return summary
        employer = self._session.get(Employer, employer_id)
        return EmployerOutcomeSummary(
            employer_id=str(employer_id),
            employer_name=employer.name if employer else "",
            applications_sent=0,
            interviews=0,
            offers=0,
            rejections=0,
        )

    def interview_analytics_summary(self, user_id: uuid.UUID) -> InterviewAnalyticsSummary:
        """Account-wide interview analytics — built from the real
        `InterviewRecord` table (see `analytics_models.py`'s module
        docstring for why this is separate from `EmployerOutcomeSummary`)."""
        interviews = list(self._session.scalars(select(InterviewRecord).where(InterviewRecord.user_id == user_id)))

        scheduled = sum(1 for i in interviews if i.status in _INTERVIEW_UPCOMING_STATUSES)
        completed = sum(1 for i in interviews if i.status in _INTERVIEW_COMPLETED_STATUSES)
        cancelled = sum(1 for i in interviews if i.status in _INTERVIEW_CANCELLED_STATUSES)
        offers = sum(1 for i in interviews if i.status == InterviewStatus.OFFER_RECEIVED.value)

        offer_conversion_rate = round(100 * offers / completed, 1) if completed else 0.0
        booked = completed + cancelled
        interview_success_rate = round(100 * completed / booked, 1) if booked else 0.0

        days_to_interview = self._average_days_application_to_interview(interviews)
        avg_interviews_before_offer = self._average_interviews_before_offer(interviews)
        most_successful_employer = self._most_successful_employer(interviews)

        return InterviewAnalyticsSummary(
            scheduled=scheduled,
            completed=completed,
            cancelled=cancelled,
            offer_conversion_rate=offer_conversion_rate,
            interview_success_rate=interview_success_rate,
            average_days_application_to_interview=days_to_interview,
            average_interviews_before_offer=avg_interviews_before_offer,
            most_successful_employer=most_successful_employer,
        )

    def employer_interview_stats(self, user_id: uuid.UUID, employer_id: uuid.UUID) -> EmployerInterviewStats:
        interviews = list(
            self._session.scalars(
                select(InterviewRecord).where(
                    InterviewRecord.user_id == user_id, InterviewRecord.employer_id == employer_id
                )
            )
        )
        completed = sum(1 for i in interviews if i.status in _INTERVIEW_COMPLETED_STATUSES)
        cancelled = sum(1 for i in interviews if i.status in _INTERVIEW_CANCELLED_STATUSES)
        offers = sum(1 for i in interviews if i.status == InterviewStatus.OFFER_RECEIVED.value)

        by_application: dict[uuid.UUID | None, int] = {}
        for interview in interviews:
            key = interview.application_workflow_id or interview.job_id or interview.id
            by_application[key] = by_application.get(key, 0) + 1
        average_per_application = (
            round(sum(by_application.values()) / len(by_application), 1) if by_application else None
        )

        return EmployerInterviewStats(
            employer_id=str(employer_id),
            total_interviews=len(interviews),
            completed_interviews=completed,
            cancelled_interviews=cancelled,
            offers=offers,
            average_interviews_per_application=average_per_application,
        )

    def ai_status(self, user_id: uuid.UUID) -> AIStatus:
        """Whether a real Anthropic key is configured, which model, and
        what fraction of this user's job matches were actually scored by
        that LLM (`analysis["used_llm"]`) rather than falling back to
        rule-only scoring — e.g. because matching ran before a key was
        configured, or a call failed and `MatchingEngine` fell back (see
        that class's docstring)."""
        analyses = self._session.scalars(
            select(JobMatch.analysis).where(JobMatch.user_id == user_id)
        ).all()
        matches_total = len(analyses)
        matches_with_ai = sum(1 for analysis in analyses if analysis and analysis.get("used_llm"))
        coverage = round(100 * matches_with_ai / matches_total, 1) if matches_total else 0.0
        return AIStatus(
            configured=bool(settings.anthropic_api_key),
            model=settings.anthropic_model,
            matches_total=matches_total,
            matches_with_ai=matches_with_ai,
            ai_coverage_percent=coverage,
        )

    def job_ingestion_summary(self, *, latest_limit: int = 10) -> JobIngestionSummary:
        """Account-wide job-discovery facts — never scoped by user, unlike
        most of this service's other methods (see `analytics_models.py`'s
        module docstring for why)."""
        now = utc_now()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        week_start = today_start - timedelta(days=today_start.weekday())

        jobs_discovered = self._count(select(func.count()).select_from(Job))
        jobs_today = self._count(select(func.count()).select_from(Job).where(Job.created_at >= today_start))
        jobs_this_week = self._count(select(func.count()).select_from(Job).where(Job.created_at >= week_start))

        source_rows = self._session.execute(
            select(Job.source_site, func.count()).group_by(Job.source_site).order_by(func.count().desc())
        ).all()
        jobs_by_source = tuple(NamedCount(name=source, count=count) for source, count in source_rows)

        employer_rows = self._session.execute(
            select(Employer.name, func.count(Job.id))
            .join(Job, Job.employer_id == Employer.id)
            .group_by(Employer.name)
            .order_by(func.count(Job.id).desc())
            .limit(10)
        ).all()
        top_employers = tuple(NamedCount(name=name, count=count) for name, count in employer_rows)

        latest_rows = self._session.execute(
            select(Job.id, Job.title, Employer.name, Job.source_site, Job.created_at)
            .join(Employer, Employer.id == Job.employer_id)
            .order_by(Job.created_at.desc())
            .limit(latest_limit)
        ).all()
        latest_jobs = tuple(
            LatestJobSummary(
                job_id=str(job_id), title=title, employer_name=employer_name, source=source, discovered_at=created_at
            )
            for job_id, title, employer_name, source, created_at in latest_rows
        )

        return JobIngestionSummary(
            jobs_discovered=jobs_discovered,
            jobs_today=jobs_today,
            jobs_this_week=jobs_this_week,
            jobs_by_source=jobs_by_source,
            top_employers_by_volume=top_employers,
            latest_jobs=latest_jobs,
        )

    def job_market_analytics(self) -> JobMarketAnalytics:
        """Chart data describing the whole imported job market — account-
        wide, like `job_ingestion_summary()` above, not any one user's
        matches/applications."""
        band_rows = self._session.execute(
            select(Job.band, func.count())
            .where(Job.band.is_not(None))
            .group_by(Job.band)
            .order_by(func.count().desc())
        ).all()
        jobs_by_band = tuple(NamedCount(name=band, count=count) for band, count in band_rows)

        employer_rows = self._session.execute(
            select(Employer.name, func.count(Job.id))
            .join(Job, Job.employer_id == Employer.id)
            .group_by(Employer.name)
            .order_by(func.count(Job.id).desc())
            .limit(10)
        ).all()
        jobs_by_employer = tuple(NamedCount(name=name, count=count) for name, count in employer_rows)

        location_rows = self._session.execute(
            select(Job.location, func.count())
            .where(Job.location.is_not(None))
            .group_by(Job.location)
            .order_by(func.count().desc())
            .limit(10)
        ).all()
        jobs_by_location = tuple(NamedCount(name=location, count=count) for location, count in location_rows)

        source_rows = self._session.execute(
            select(Job.source_site, func.count()).group_by(Job.source_site).order_by(func.count().desc())
        ).all()
        jobs_by_source = tuple(NamedCount(name=source, count=count) for source, count in source_rows)

        return JobMarketAnalytics(
            jobs_by_band=jobs_by_band,
            jobs_by_employer=jobs_by_employer,
            jobs_by_location=jobs_by_location,
            jobs_by_salary_bucket=self._salary_bucket_counts(),
            jobs_by_source=jobs_by_source,
            jobs_over_time=self._jobs_discovered_per_day(),
        )

    # --- internals ---

    def _salary_bucket_counts(self) -> tuple[SalaryBucketCount, ...]:
        # Aggregated in Python (like `_top_requested_skills`), keeping the
        # bucket boundaries as plain code rather than a DB-specific CASE
        # expression, for the same SQLite/Postgres portability reason.
        buckets = [
            ("Under £20k", 0, 20_000),
            ("£20k-£30k", 20_000, 30_000),
            ("£30k-£40k", 30_000, 40_000),
            ("£40k-£50k", 40_000, 50_000),
            ("£50k-£60k", 50_000, 60_000),
            ("£60k+", 60_000, None),
        ]
        counts = {label: 0 for label, _, _ in buckets}
        salaries = self._session.execute(
            select(Job.salary_min, Job.salary_max).where(
                Job.salary_min.is_not(None) | Job.salary_max.is_not(None)
            )
        ).all()
        for salary_min, salary_max in salaries:
            value = float(salary_min if salary_min is not None else salary_max)
            for label, low, high in buckets:
                if value >= low and (high is None or value < high):
                    counts[label] += 1
                    break
        return tuple(SalaryBucketCount(label=label, count=counts[label]) for label, _, _ in buckets)

    def _jobs_discovered_per_day(self, *, days: int = 30) -> tuple[MonthlyCount, ...]:
        """Daily discovery counts for the last `days` days — reuses
        `MonthlyCount`'s shape (a label + a count) with a "YYYY-MM-DD"
        label instead of "YYYY-MM"; a dedicated dataclass for an identical
        two-field shape would be pure ceremony."""
        cutoff = utc_now() - timedelta(days=days)
        rows = self._session.execute(select(Job.created_at).where(Job.created_at >= cutoff)).scalars().all()
        counts: Counter[str] = Counter(created_at.strftime("%Y-%m-%d") for created_at in rows)
        return tuple(MonthlyCount(month=day, count=count) for day, count in sorted(counts.items()))

    def _average_days_application_to_interview(self, interviews: list[InterviewRecord]) -> float | None:
        durations = []
        for interview in interviews:
            if interview.application_workflow_id is None:
                continue
            workflow = self._session.get(ApplicationWorkflowRecord, interview.application_workflow_id)
            if workflow is None:
                continue
            applied_at = workflow.created_at
            scheduled_at = interview.scheduled_at
            # Both may be naive or aware depending on the DB backend — compare dates only to sidestep that.
            delta_days = (scheduled_at.date() - applied_at.date()).days
            durations.append(delta_days)
        if not durations:
            return None
        return round(sum(durations) / len(durations), 1)

    def _average_interviews_before_offer(self, interviews: list[InterviewRecord]) -> float | None:
        by_application: dict[uuid.UUID, list[InterviewRecord]] = {}
        for interview in interviews:
            if interview.application_workflow_id is None:
                continue
            by_application.setdefault(interview.application_workflow_id, []).append(interview)

        counts = [
            len(group)
            for group in by_application.values()
            if any(i.status == InterviewStatus.OFFER_RECEIVED.value for i in group)
        ]
        if not counts:
            return None
        return round(sum(counts) / len(counts), 1)

    def _most_successful_employer(self, interviews: list[InterviewRecord]) -> str | None:
        offer_counts: Counter[uuid.UUID] = Counter(
            i.employer_id for i in interviews if i.status == InterviewStatus.OFFER_RECEIVED.value
        )
        if not offer_counts:
            return None
        top_employer_id, _ = offer_counts.most_common(1)[0]
        employer = self._session.get(Employer, top_employer_id)
        return employer.name if employer else None

    def _build_employer_outcome_summary(self, employer_id: uuid.UUID, values: dict) -> EmployerOutcomeSummary:
        applications_sent = values["applied"]
        interviews = values["interview"]
        offers = values["offer"]
        interview_rate = round(100 * interviews / applications_sent, 1) if applications_sent else 0.0
        offer_rate = round(100 * offers / applications_sent, 1) if applications_sent else 0.0
        return EmployerOutcomeSummary(
            employer_id=str(employer_id),
            employer_name=values["name"],
            applications_sent=applications_sent,
            interviews=interviews,
            offers=offers,
            rejections=values["rejected"],
            interview_rate=interview_rate,
            offer_rate=offer_rate,
        )

    def _count(self, stmt) -> int:
        return self._session.scalar(stmt) or 0

    def _count_status_history_events(self, user_id: uuid.UUID, to_status: str) -> int:
        return self._count(
            select(func.count())
            .select_from(WorkflowStatusHistoryRecord)
            .join(
                ApplicationWorkflowRecord,
                ApplicationWorkflowRecord.id == WorkflowStatusHistoryRecord.workflow_id,
            )
            .where(
                ApplicationWorkflowRecord.user_id == user_id,
                WorkflowStatusHistoryRecord.to_status == to_status,
            )
        )

    def _monthly_counts(self, model, user_id: uuid.UUID) -> tuple[MonthlyCount, ...]:
        rows = self._session.execute(select(model.created_at).where(model.user_id == user_id)).scalars().all()
        counts: Counter[str] = Counter(created_at.strftime("%Y-%m") for created_at in rows)
        return tuple(MonthlyCount(month=month, count=count) for month, count in sorted(counts.items()))

    def _match_score_distribution(self, user_id: uuid.UUID) -> tuple[ScoreBucket, ...]:
        scores = (
            self._session.execute(select(JobMatch.match_score).where(JobMatch.user_id == user_id)).scalars().all()
        )
        bucket_counts = {label: 0 for label in _SCORE_BUCKETS}
        for score in scores:
            score_value = float(score)
            index = min(int(score_value // 20), 4)  # 100 falls into the last bucket, not a 6th one
            bucket_counts[_SCORE_BUCKETS[index]] += 1
        return tuple(ScoreBucket(label=label, count=count) for label, count in bucket_counts.items())

    def _workflow_stage_rate(self, user_id: uuid.UUID, to_status: str) -> float:
        total = self._count(
            select(func.count()).select_from(ApplicationWorkflowRecord).where(ApplicationWorkflowRecord.user_id == user_id)
        )
        if total == 0:
            return 0.0
        reached = self._count(
            select(func.count(func.distinct(WorkflowStatusHistoryRecord.workflow_id)))
            .select_from(WorkflowStatusHistoryRecord)
            .join(
                ApplicationWorkflowRecord,
                ApplicationWorkflowRecord.id == WorkflowStatusHistoryRecord.workflow_id,
            )
            .where(
                ApplicationWorkflowRecord.user_id == user_id,
                WorkflowStatusHistoryRecord.to_status == to_status,
            )
        )
        return round(100 * reached / total, 1)

    def _document_decision_rate(self, user_id: uuid.UUID, status: str) -> float:
        decided = self._count(
            select(func.count())
            .select_from(GeneratedDocumentRecord)
            .where(GeneratedDocumentRecord.user_id == user_id, GeneratedDocumentRecord.status.in_(["approved", "rejected"]))
        )
        if decided == 0:
            return 0.0
        matching = self._count(
            select(func.count())
            .select_from(GeneratedDocumentRecord)
            .where(GeneratedDocumentRecord.user_id == user_id, GeneratedDocumentRecord.status == status)
        )
        return round(100 * matching / decided, 1)

    def _top_employers(self, user_id: uuid.UUID, *, limit: int = 10) -> tuple[NamedCount, ...]:
        rows = self._session.execute(
            select(Employer.name, func.count(JobMatch.id))
            .join(Job, Job.employer_id == Employer.id)
            .join(JobMatch, JobMatch.job_id == Job.id)
            .where(JobMatch.user_id == user_id)
            .group_by(Employer.name)
            .order_by(func.count(JobMatch.id).desc())
            .limit(limit)
        ).all()
        return tuple(NamedCount(name=name, count=count) for name, count in rows)

    def _top_requested_skills(self, user_id: uuid.UUID, *, limit: int = 15) -> tuple[NamedCount, ...]:
        rows = self._session.execute(
            select(Job.requirements)
            .join(JobMatch, JobMatch.job_id == Job.id)
            .where(JobMatch.user_id == user_id, Job.requirements.is_not(None))
        ).scalars().all()
        # Aggregated in Python rather than via a DB-side JSON-array-unnest
        # query: SQLite's JSON functions for this differ from Postgres's,
        # and this project aims to stay portable between the two.
        counter: Counter[str] = Counter()
        for requirements in rows:
            counter.update(requirements)
        return tuple(NamedCount(name=name, count=count) for name, count in counter.most_common(limit))

    def _pipeline_stage_counts(self, user_id: uuid.UUID) -> tuple[PipelineStageCount, ...]:
        rows = dict(
            self._session.execute(
                select(SavedJob.pipeline_stage, func.count())
                .where(SavedJob.user_id == user_id)
                .group_by(SavedJob.pipeline_stage)
            ).all()
        )
        # Every stage is always present (as 0 if empty) so the dashboard's
        # chart doesn't need to special-case missing stages.
        return tuple(PipelineStageCount(stage=stage.value, count=rows.get(stage.value, 0)) for stage in PipelineStage)

    def _favourite_employers(self, user_id: uuid.UUID, *, limit: int = 10) -> tuple[NamedCount, ...]:
        rows = self._session.execute(
            select(Employer.name, func.count(SavedJob.id))
            .join(Job, Job.employer_id == Employer.id)
            .join(SavedJob, SavedJob.job_id == Job.id)
            .where(SavedJob.user_id == user_id, SavedJob.is_favourite.is_(True))
            .group_by(Employer.name)
            .order_by(func.count(SavedJob.id).desc())
            .limit(limit)
        ).all()
        return tuple(NamedCount(name=name, count=count) for name, count in rows)
