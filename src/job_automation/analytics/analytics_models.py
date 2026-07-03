"""
Value objects returned by `AnalyticsService` — dependency-free dataclasses,
same pattern as every other `*_models.py` in this project (`ai
.matching_models`, `documents.document_models`, `workflows.workflow_models`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime


@dataclass(frozen=True)
class DashboardSummary:
    jobs_discovered: int
    jobs_matched: int
    applications: int
    pending_review: int
    approved_documents: int
    rejected_documents: int
    interview_invitations: int
    offers: int


@dataclass(frozen=True)
class ActivityItem:
    action: str
    details: dict
    occurred_at: datetime
    job_title: str | None = None
    employer_name: str | None = None


@dataclass(frozen=True)
class UpcomingDeadline:
    job_id: str
    job_title: str
    employer_name: str
    closing_date: date


@dataclass(frozen=True)
class MonthlyCount:
    month: str  # "YYYY-MM"
    count: int


@dataclass(frozen=True)
class ScoreBucket:
    label: str  # e.g. "80-100"
    count: int


@dataclass(frozen=True)
class NamedCount:
    name: str
    count: int


@dataclass(frozen=True)
class AnalyticsReport:
    applications_per_month: tuple[MonthlyCount, ...] = field(default_factory=tuple)
    match_score_distribution: tuple[ScoreBucket, ...] = field(default_factory=tuple)
    interview_rate: float = 0.0
    offer_rate: float = 0.0
    approval_rate: float = 0.0
    rejection_rate: float = 0.0
    documents_generated_per_month: tuple[MonthlyCount, ...] = field(default_factory=tuple)
    top_employers: tuple[NamedCount, ...] = field(default_factory=tuple)
    top_requested_skills: tuple[NamedCount, ...] = field(default_factory=tuple)


# --- Added for the Job Management milestone ---
#
# `JobOrganizationSummary` is deliberately a *separate* report from
# `DashboardSummary`/`AnalyticsReport` above, not an extension of them.
# Those two are built entirely from the AI-matching-driven pipeline
# (`JobMatch`, `ApplicationWorkflowRecord`, `WorkflowStatusHistoryRecord`).
# This one is built from the user-driven Kanban board (`SavedJob`,
# `JobReminder`) — a candidate can save/favourite/move a job through
# "Interested -> Documents Ready -> Applied -> ..." without any AI match
# or workflow record ever existing for it. Merging the two into one report
# would force a false either/or between two genuinely different data
# sources, the same reasoning that kept `PipelineStage` a separate enum
# from `WorkflowStatus` (see `job_organization_models.py`).
#
# "Recent activity" for the job-organization dashboard is intentionally
# NOT duplicated here: pipeline-stage transitions and due reminders are
# already published as real notifications (`NotificationService`), so the
# dashboard's recent-activity feed reuses `NotificationService.list_notifications()`
# directly rather than this service re-deriving the same feed a second way.


@dataclass(frozen=True)
class PipelineStageCount:
    stage: str
    count: int


@dataclass(frozen=True)
class JobOrganizationSummary:
    jobs_saved: int
    jobs_favourited: int
    jobs_archived: int
    applications: int  # jobs at or past the "Applied" pipeline stage
    interviews: int
    offers: int
    rejected: int
    upcoming_deadlines: int
    upcoming_reminders: int
    stage_counts: tuple[PipelineStageCount, ...] = field(default_factory=tuple)
    favourite_employers: tuple[NamedCount, ...] = field(default_factory=tuple)


# --- Added for the Employer & Application CRM milestone ---
#
# `EmployerOutcomeSummary` deliberately draws from *two* different existing
# data sources, not one — see `AnalyticsService.employer_outcome_summary()`'s
# docstring for the full reasoning. In short: `applications_sent`/
# `interviews`/`offers` come from `WorkflowStatusHistoryRecord` (the
# formal, document-review-gated pipeline, already the authoritative source
# for these three counts elsewhere in this file), while `rejections` comes
# from `SavedJob.pipeline_stage == PipelineStage.REJECTED` — the *only*
# place in this schema where "the employer rejected this candidate" is
# actually recorded. `WorkflowStatus.REJECTED` means something else
# entirely (a reviewer rejected the drafted document; see
# `job_organization_models.py`'s docstring), so it would be a genuine
# correctness bug to count it as an employer rejection here.


@dataclass(frozen=True)
class EmployerOutcomeSummary:
    employer_id: str
    employer_name: str
    applications_sent: int
    interviews: int
    offers: int
    rejections: int
    interview_rate: float = 0.0  # interviews / applications_sent, as a percentage
    offer_rate: float = 0.0  # offers / applications_sent, as a percentage


# --- Added for the Interview & Calendar Management milestone ---
#
# `InterviewAnalyticsSummary`/`EmployerInterviewStats` are built from the
# real `InterviewRecord` table — a different, more detailed data source
# than `EmployerOutcomeSummary.interviews` above (which is a *count of
# workflow-history transitions to "interview"*, established in the
# Employer CRM milestone before `InterviewRecord` existed). Deliberately
# NOT unified into `EmployerOutcomeSummary`: that field stays exactly what
# it already was (and every existing test asserting it keeps passing),
# while these two new types answer richer questions only the dedicated
# `InterviewRecord` table can — how many interviews are still upcoming,
# how many were cancelled/missed vs. actually completed, how many rounds
# a typical application goes through, etc.


@dataclass(frozen=True)
class InterviewAnalyticsSummary:
    scheduled: int  # status in {scheduled, upcoming, rescheduled} — still to come
    completed: int  # status in {completed, offer_received, rejected, waiting_decision} — the interview happened
    cancelled: int  # status in {cancelled, missed}
    offer_conversion_rate: float = 0.0  # offer_received / completed, as a percentage
    interview_success_rate: float = 0.0  # completed / (completed + cancelled), as a percentage — did booked interviews actually happen
    average_days_application_to_interview: float | None = None
    average_interviews_before_offer: float | None = None
    most_successful_employer: str | None = None  # the employer with the most offers


@dataclass(frozen=True)
class EmployerInterviewStats:
    employer_id: str
    total_interviews: int
    completed_interviews: int
    cancelled_interviews: int
    offers: int
    average_interviews_per_application: float | None = None


# --- Added for the Anthropic AI Integration milestone ---
#
# Deliberately computed from data that already exists (`settings
# .anthropic_api_key`, `JobMatch.analysis["used_llm"]`) rather than a new
# usage-tracking table — see `ai.anthropic_provider.AnthropicProvider`'s
# module docstring for why cost/token logging is a log line, not a table;
# the same reasoning applies here.


@dataclass(frozen=True)
class AIStatus:
    configured: bool  # settings.anthropic_api_key is set
    model: str
    matches_total: int
    matches_with_ai: int  # analysis["used_llm"] is True
    ai_coverage_percent: float = 0.0  # matches_with_ai / matches_total, as a percentage


# --- Added for the Job Ingestion Service milestone ---
#
# Account-wide facts about the *job market as discovered*, not any one
# user's applications/matches — deliberately a separate report from
# `DashboardSummary` (which is already partly user-scoped: `jobs_matched`,
# `applications`, etc.) for the same reason `JobOrganizationSummary` stays
# separate: this is about what's been imported, not what one candidate has
# done with it. `dashboard_summary.jobs_discovered` (unscoped `Job` count)
# already existed before this milestone; the fields here are additive,
# never a replacement for it.


@dataclass(frozen=True)
class LatestJobSummary:
    job_id: str
    title: str
    employer_name: str
    source: str
    discovered_at: datetime


@dataclass(frozen=True)
class JobIngestionSummary:
    jobs_discovered: int
    jobs_today: int
    jobs_this_week: int
    jobs_by_source: tuple[NamedCount, ...] = field(default_factory=tuple)
    top_employers_by_volume: tuple[NamedCount, ...] = field(default_factory=tuple)
    latest_jobs: tuple[LatestJobSummary, ...] = field(default_factory=tuple)


# --- Added for the Job Ingestion Service milestone: market-wide analytics
# charts. Deliberately unscoped by user (unlike `AnalyticsReport`, which is
# built from one candidate's matches/applications) — these describe the
# whole imported job market, the same "account-wide, not per-user" scope
# `JobIngestionSummary` above uses.


@dataclass(frozen=True)
class SalaryBucketCount:
    label: str  # e.g. "20k-30k"
    count: int


@dataclass(frozen=True)
class JobMarketAnalytics:
    jobs_by_band: tuple[NamedCount, ...] = field(default_factory=tuple)
    jobs_by_employer: tuple[NamedCount, ...] = field(default_factory=tuple)
    jobs_by_location: tuple[NamedCount, ...] = field(default_factory=tuple)
    jobs_by_salary_bucket: tuple[SalaryBucketCount, ...] = field(default_factory=tuple)
    jobs_by_source: tuple[NamedCount, ...] = field(default_factory=tuple)
    jobs_over_time: tuple[MonthlyCount, ...] = field(default_factory=tuple)
