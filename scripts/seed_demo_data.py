"""
Populates `data/jobs.db` with realistic demo data for manually exercising
the web dashboard: one candidate, two employers, five jobs, AI matches
across a spread of scores, and application workflows at several different
stages (a fresh match, an in-review rejection/regeneration loop, an
in-progress interview, an untouched low-scoring match, and a closed one).

Every row is created through the *existing* services this milestone was
told to reuse — `ProfileService`, `MatchingEngine`/`MatchingService`,
`DocumentService`, `WorkflowService` — not by hand-building rows that skip
their business logic. A `FakeLLMProvider` (same pattern as the test suite)
stands in for a real Anthropic call so this script never needs an API key.

Refuses to run if a `User` already exists, so it can't silently duplicate
demo data on top of real or previously-seeded data — delete `data/jobs.db`
first (or point `DATABASE_URL` elsewhere) to reseed from scratch.
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sqlalchemy import select

from job_automation.ai.llm_provider import LLMProvider
from job_automation.ai.matching_engine import MatchingEngine
from job_automation.auth.auth_service import AuthService
from job_automation.ai.matching_models import CandidateProfile as AICandidateProfile
from job_automation.ai.matching_models import ExperienceEntry as AIExperienceEntry
from job_automation.ai.matching_models import JobSnapshot
from job_automation.ai.matching_service import MatchingService
from job_automation.database.db_manager import get_session, init_db
from job_automation.database.models import Employer, Job, User
from job_automation.documents.document_service import DocumentService
from job_automation.profile.candidate_profile import (
    CandidateProfile,
    PersonalInformation,
    VisaStatus,
)
from job_automation.profile.certificate_parser import Certificate
from job_automation.profile.education_parser import EducationEntry
from job_automation.profile.employment_history import EmploymentEntry
from job_automation.profile.preference_manager import CandidatePreferences
from job_automation.profile.profile_service import ProfileService
from job_automation.workflows.workflow_service import WorkflowService


class FakeLLMProvider(LLMProvider):
    """Same test double used throughout `tests/` — deterministic text, no
    network call, so this script never requires `ANTHROPIC_API_KEY`."""

    def __init__(self, response: str = "I am committed to providing safe, compassionate, person-centred care.") -> None:
        self._response = response

    def complete(self, *, system_prompt: str, user_prompt: str, max_tokens: int = 1024) -> str:
        return self._response


def build_rich_profile() -> CandidateProfile:
    """The `profile.candidate_profile.CandidateProfile` used by the
    Candidate Profile page and document generation."""
    return CandidateProfile(
        personal_information=PersonalInformation(
            full_name="Jane Doe",
            email="jane.doe@example.com",
            phone="07700 900123",
            address="12 Elm Street, London, E1 6AN",
            personal_statement=(
                "Compassionate, reliable healthcare assistant with three years' experience "
                "supporting older adults and people living with dementia in NHS and residential "
                "care settings."
            ),
        ),
        education=(
            EducationEntry(qualification="NVQ Level 2 in Health and Social Care", awarding_body="City & Guilds", year="2021"),
            EducationEntry(qualification="GCSEs (English, Maths, Science)", awarding_body="AQA", year="2018"),
        ),
        employment_history=(
            EmploymentEntry(
                job_title="Healthcare Assistant",
                employer="Riverside NHS Foundation Trust",
                location="London",
                start_date="2023-02-01",
                end_date=None,
                responsibilities=(
                    "Supporting patients with personal care, mobility, and daily living activities",
                    "Monitoring and recording vital signs",
                    "Assisting nursing staff with safeguarding and dignity-focused care",
                ),
            ),
            EmploymentEntry(
                job_title="Care Assistant",
                employer="Oakfield Residential Care Home",
                location="London",
                start_date="2021-06-01",
                end_date="2023-01-31",
                responsibilities=(
                    "Providing person-centred dementia care",
                    "Supporting residents with medication administration under supervision",
                    "Communicating with families and the wider care team",
                ),
            ),
        ),
        skills=(
            "patient care", "manual handling", "medication administration", "communication",
            "safeguarding", "dementia care", "infection control", "record keeping",
        ),
        certificates=(
            Certificate(name="Enhanced DBS Check", issued_date="2024-01-15"),
            Certificate(name="Manual Handling", issuing_body="St John Ambulance", issued_date="2024-03-01"),
            Certificate(name="Basic Life Support", issuing_body="Resuscitation Council UK", issued_date="2024-03-01"),
            Certificate(name="Safeguarding Adults Level 2", issued_date="2023-11-20"),
        ),
        professional_registrations=(),
        languages=(),
        visa_status=VisaStatus(right_to_work_uk=True, sponsorship_required=False),
        preferences=CandidatePreferences(
            preferred_locations=("London",),
            max_travel_distance_miles=15,
            preferred_employers=("Riverside NHS Foundation Trust",),
            preferred_contract_type="Permanent",
            preferred_hours="Full-time",
            preferred_salary_min=22000,
            preferred_nhs_band="Band 3",
            preferred_working_pattern="Full-time",
            remote_preference="on-site",
            visa_sponsorship_required=False,
        ),
        career_goals=("Progress toward a Nursing Associate apprenticeship within the NHS",),
        availability="2 weeks' notice",
        keywords=("NHS", "care", "compassion", "dignity", "person-centred care"),
    )


def build_ai_profile() -> AICandidateProfile:
    """The separate, lighter `ai.matching_models.CandidateProfile` the
    matching engine consumes — see docs/AI_MATCHING.md / CANDIDATE_PROFILE.md
    for why the two representations coexist rather than being merged."""
    return AICandidateProfile(
        skills=(
            "patient care", "manual handling", "medication administration", "communication",
            "safeguarding", "dementia care", "infection control",
        ),
        experience=(
            AIExperienceEntry(
                job_title="Healthcare Assistant",
                employer="Riverside NHS Foundation Trust",
                responsibilities=("personal care", "mobility support", "vital signs monitoring"),
            ),
            AIExperienceEntry(
                job_title="Care Assistant",
                employer="Oakfield Residential Care Home",
                responsibilities=("dementia care", "medication administration"),
            ),
        ),
        certificates=("Enhanced DBS Check", "Manual Handling", "Basic Life Support", "Safeguarding Adults Level 2"),
        preferred_locations=("London",),
        preferred_salary_min=22000,
        preferred_band="Band 3",
        visa_sponsorship_required=False,
        working_pattern_preference="Full-time",
        keywords=("NHS", "care", "compassion", "dignity"),
    )


def seed_employers_and_jobs(session) -> dict[str, Job]:
    riverside = Employer(name="Riverside NHS Foundation Trust", website="https://example-riverside-nhs.example")
    oakfield = Employer(name="Oakfield Residential Care Home", website="https://example-oakfield.example")
    meadow_view = Employer(name="Meadow View Care Group", website="https://example-meadowview.example")
    session.add_all([riverside, oakfield, meadow_view])
    session.flush()

    today = date.today()
    jobs = {
        "healthcare_assistant": Job(
            employer=riverside, title="Healthcare Assistant", source_site="nhs_jobs", external_id="DEMO-001",
            url="https://example-riverside-nhs.example/jobs/1",
            description="Provide compassionate personal care to inpatients on a busy medical ward.",
            location="London", salary_min=22383, salary_max=24336, salary_period="per annum", band="Band 2",
            contract_type="Permanent", working_pattern="Full-time", visa_sponsorship=True,
            closing_date=today + timedelta(days=21),
            requirements=["Enhanced DBS check", "Care experience", "Good communication skills"],
            benefits=["NHS pension", "27 days annual leave"],
        ),
        "senior_healthcare_assistant": Job(
            employer=riverside, title="Senior Healthcare Assistant", source_site="nhs_jobs", external_id="DEMO-002",
            url="https://example-riverside-nhs.example/jobs/2",
            description="Senior HCA role leading care delivery and mentoring junior assistants.",
            location="London", salary_min=24336, salary_max=26302, salary_period="per annum", band="Band 3",
            contract_type="Permanent", working_pattern="Full-time", visa_sponsorship=True,
            closing_date=today + timedelta(days=14),
            requirements=["NVQ Level 3 or equivalent", "2+ years care experience"],
            benefits=["NHS pension", "Career progression"],
        ),
        "support_worker": Job(
            employer=oakfield, title="Support Worker", source_site="reed", external_id="DEMO-003",
            url="https://example-oakfield.example/jobs/3",
            description="Support residents with daily living, dignity, and wellbeing in a residential setting.",
            location="Manchester", salary_min=21000, salary_max=22500, salary_period="per annum", band=None,
            contract_type="Permanent", working_pattern="Full-time", visa_sponsorship=None,
            closing_date=today + timedelta(days=30),
            requirements=["Care certificate desirable", "Right to work in the UK"],
            benefits=["Paid training"],
        ),
        "registered_nurse": Job(
            employer=riverside, title="Registered Nurse", source_site="nhs_jobs", external_id="DEMO-004",
            url="https://example-riverside-nhs.example/jobs/4",
            description="Deliver clinical nursing care on a busy acute ward. NMC registration required.",
            location="London", salary_min=29970, salary_max=36483, salary_period="per annum", band="Band 5",
            contract_type="Permanent", working_pattern="Full-time", visa_sponsorship=True,
            closing_date=today + timedelta(days=21),
            requirements=["Active NMC registration", "Degree in Nursing"],
            benefits=["NHS pension", "Relocation package"],
        ),
        "domiciliary_care_assistant": Job(
            employer=meadow_view, title="Domiciliary Care Assistant", source_site="indeed", external_id="DEMO-005",
            url="https://example-meadowview.example/jobs/5",
            description="Community-based care visits supporting independent living.",
            location="Birmingham", salary_min=20000, salary_max=21000, salary_period="per annum", band=None,
            contract_type="Zero-hours", working_pattern="Flexible", visa_sponsorship=False,
            closing_date=today - timedelta(days=5),
            is_active=False,
            requirements=["Driving licence", "Own vehicle"],
            benefits=["Mileage allowance"],
        ),
    }
    session.add_all(jobs.values())
    session.flush()
    return jobs


#: Demo login credentials — printed at the end of this script's output.
#: Not a secret worth protecting: this seeds an obviously-fake local dev
#: account, never anything resembling production data.
DEMO_EMAIL = "jane.doe@example.com"
DEMO_PASSWORD = "DemoPassword123!"


def main() -> None:
    init_db()

    with get_session() as session:
        existing_user = session.scalars(select(User)).first()
        if existing_user is not None:
            print(f"A User already exists ({existing_user.email}) — refusing to reseed. "
                  "Delete data/jobs.db first if you want a fresh demo dataset.")
            return

        user = AuthService(session).register(email=DEMO_EMAIL, password=DEMO_PASSWORD, full_name="Jane Doe")
        user.right_to_work_uk = True
        session.flush()

        ProfileService(session).save(build_rich_profile(), user_id=user.id)

        jobs = seed_employers_and_jobs(session)

        engine = MatchingEngine()  # rule-only: no LLMProvider configured, matching docs/AI_MATCHING.md's design
        matching_service = MatchingService(session, engine)
        ai_profile = build_ai_profile()
        matches = {
            key: matching_service.evaluate_job(job, ai_profile, user_id=user.id)
            for key, job in jobs.items()
        }
        session.commit()

        doc_service = DocumentService(session, FakeLLMProvider())
        workflow_service = WorkflowService(session)
        rich_profile = build_rich_profile()

        # 1. Healthcare Assistant: full journey through to an active interview.
        job = jobs["healthcare_assistant"]
        match = matches["healthcare_assistant"]
        workflow = workflow_service.start_workflow(user_id=user.id, job_id=job.id, job_match_id=match.id)
        snapshot = JobSnapshot.from_job(job)
        document = doc_service.generate_supporting_statement(
            rich_profile, snapshot, user_id=user.id, job_id=job.id
        )
        session.commit()
        workflow = workflow_service.attach_document(workflow, document)
        workflow = workflow_service.submit_for_review(workflow)
        workflow = workflow_service.approve(workflow, reviewer_notes="Strong, specific supporting statement.")
        workflow = workflow_service.mark_ready_to_apply(workflow)
        workflow = workflow_service.mark_applied(workflow, note="Applied via NHS Jobs on the trust's website.")
        workflow_service.mark_interview(workflow, note="Interview scheduled for next Tuesday, 10am.")
        session.commit()

        # 2. Senior Healthcare Assistant: rejected once, regenerated, awaiting resubmission.
        job = jobs["senior_healthcare_assistant"]
        match = matches["senior_healthcare_assistant"]
        workflow = workflow_service.start_workflow(user_id=user.id, job_id=job.id, job_match_id=match.id)
        snapshot = JobSnapshot.from_job(job)
        document = doc_service.generate_cover_letter(rich_profile, snapshot, user_id=user.id, job_id=job.id)
        session.commit()
        workflow = workflow_service.attach_document(workflow, document)
        workflow = workflow_service.submit_for_review(workflow)
        workflow = workflow_service.reject(workflow, reviewer_notes="Too generic — mention mentoring experience.")
        second_document = doc_service.generate_cover_letter(
            rich_profile, snapshot, user_id=user.id, job_id=job.id
        )
        session.commit()
        workflow_service.attach_document(workflow, second_document)
        session.commit()

        # 3. Support Worker: a fresh, untouched match.
        job = jobs["support_worker"]
        match = matches["support_worker"]
        workflow_service.start_workflow(user_id=user.id, job_id=job.id, job_match_id=match.id)
        session.commit()

        # 4. Registered Nurse: matched (low score — no NMC registration) but never pursued.
        #    Deliberately no workflow started, demonstrating a match the candidate skipped.

        # 5. Domiciliary Care Assistant: pursued, then closed (listing withdrawn).
        job = jobs["domiciliary_care_assistant"]
        match = matches["domiciliary_care_assistant"]
        workflow = workflow_service.start_workflow(user_id=user.id, job_id=job.id, job_match_id=match.id)
        workflow_service.close(workflow, reason="Listing withdrawn by employer before application was submitted.")
        session.commit()

    print("Demo data seeded: 1 candidate, 3 employers, 5 jobs, 5 AI matches, 4 workflows "
          "(interview / rejected-and-regenerated / new / closed), 1 unmatched-pursuit job.")
    print(f"Log in at /login with: {DEMO_EMAIL} / {DEMO_PASSWORD}")
    print("Run the dashboard with: uvicorn job_automation.web.app:app --reload")


if __name__ == "__main__":
    main()
