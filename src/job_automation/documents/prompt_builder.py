"""
Prompt construction for document generation — one system/user prompt pair
per document type, all sharing a common grounding-rules preamble.

Reuses `job_automation.ai.matching_models.JobSnapshot`/`MatchResult` for job
and match context, and `job_automation.profile.candidate_profile
.CandidateProfile` for candidate context, rather than inventing new types —
this milestone's whole point is bringing those three already-built
subsystems together, not duplicating them.

Rendering a `CandidateProfile` into prompt text lives here, not on
`CandidateProfile` itself: "how a profile should be presented to an LLM for
document generation" is a document-generation concern, not something the
profile subsystem (built to be a neutral structured representation used by
*any* future consumer) should own.
"""

from __future__ import annotations

from job_automation.ai.matching_models import JobSnapshot, MatchResult
from job_automation.profile.candidate_profile import CandidateProfile

#: Shared by every document type's system prompt — this is what implements
#: "avoid inventing experience not in the profile" (grounding) and
#: "highlight skills/certificates/NHS care experience/safeguarding/
#: communication/teamwork/confidentiality/dignity/person-centred care"
#: (thematic emphasis) at the prompt level. `DocumentValidator` is the
#: post-hoc backstop for when a prompt instruction alone isn't followed.
_GROUNDING_RULES = """CRITICAL RULES — follow these exactly:
1. Only reference skills, qualifications, certificates, employers, and experience explicitly present in the candidate profile below. Do not invent, assume, or embellish any experience, dates, employers, or qualifications that are not stated.
2. If the profile lacks information to address a particular point well, write around it honestly rather than fabricating detail.
3. Where truthful and relevant, naturally incorporate themes valued in UK health and social care: safeguarding, effective communication, teamwork, confidentiality, dignity and respect, and person-centred care — but only in ways grounded in the candidate's actual listed skills, certificates, and experience.
4. Write in a professional, warm, genuine tone suitable for a UK healthcare-sector job application."""


def _render_profile(profile: CandidateProfile) -> str:
    lines = [f"Full name: {profile.personal_information.full_name}"]
    if profile.personal_information.personal_statement:
        lines.append(f"Personal statement: {profile.personal_information.personal_statement}")

    lines.append(f"Skills: {', '.join(profile.skills) or 'none listed'}")

    if profile.employment_history:
        lines.append("Employment history:")
        for entry in profile.employment_history:
            span = f"{entry.start_date or '?'} to {entry.end_date or 'present'}"
            lines.append(f"  - {entry.job_title} at {entry.employer or 'unknown employer'} ({span})")
            for responsibility in entry.responsibilities:
                lines.append(f"      * {responsibility}")

    if profile.education:
        lines.append("Education:")
        lines.extend(f"  - {entry.qualification} ({entry.awarding_body or 'unknown body'})" for entry in profile.education)

    if profile.certificates:
        lines.append("Certificates:")
        lines.extend(f"  - {cert.name}" for cert in profile.certificates)

    if profile.professional_registrations:
        lines.append("Professional registrations:")
        lines.extend(
            f"  - {reg.body} (registration number {reg.registration_number})"
            for reg in profile.professional_registrations
        )

    if profile.languages:
        lines.append(
            "Languages: " + ", ".join(f"{lang.language} ({lang.proficiency})" for lang in profile.languages)
        )

    if profile.career_goals:
        lines.append("Career goals: " + "; ".join(profile.career_goals))

    return "\n".join(lines)


def _render_job(job: JobSnapshot) -> str:
    lines = [
        f"Title: {job.title}",
        f"Employer: {job.employer or 'unknown'}",
        f"Location: {job.location or 'unknown'}",
        f"Band: {job.band or 'not specified'}",
        f"Contract type: {job.contract_type or 'not specified'}",
        f"Working pattern: {job.working_pattern or 'not specified'}",
    ]
    if job.description:
        lines.append(f"Description: {job.description}")
    if job.requirements:
        lines.append("Requirements: " + "; ".join(job.requirements))
    return "\n".join(lines)


def _render_match(match_result: MatchResult | None) -> str:
    if match_result is None:
        return ""
    lines = ["Match analysis for this role:"]
    if match_result.strengths:
        lines.append("Strengths to emphasise: " + "; ".join(match_result.strengths))
    if match_result.matched_keywords:
        lines.append("Matched keywords: " + ", ".join(match_result.matched_keywords))
    return "\n".join(lines)


# --- Supporting statement -----------------------------------------------------


def build_supporting_statement_system_prompt() -> str:
    return f"""You are an expert UK healthcare careers advisor helping a candidate write an NHS-style supporting statement (personal statement) for a specific job application.

{_GROUNDING_RULES}

An NHS-style supporting statement should:
- Open by expressing genuine interest in this specific role and organisation.
- Address relevant skills, qualifications, and experience that match the role, drawing a clear line between what the candidate has done and what the role needs.
- Reference specific responsibilities or achievements from the candidate's employment history where relevant.
- Close by summarising what the candidate would bring to the team and their enthusiasm for the role.

Write 3-5 flowing paragraphs of prose (no bullet points, no headings), around 400-600 words. Do not include a greeting or sign-off ("Dear...", "Yours sincerely") — this is a standalone document, not a letter."""


def build_supporting_statement_user_prompt(
    profile: CandidateProfile, job: JobSnapshot, match_result: MatchResult | None = None
) -> str:
    parts = [
        "Candidate profile:",
        _render_profile(profile),
        "",
        "Job listing:",
        _render_job(job),
    ]
    match_text = _render_match(match_result)
    if match_text:
        parts += ["", match_text]
    parts += ["", "Write the supporting statement now."]
    return "\n".join(parts)


# --- Cover letter --------------------------------------------------------------


def build_cover_letter_system_prompt() -> str:
    return f"""You are an expert UK healthcare careers advisor helping a candidate write a cover letter for a specific job application.

{_GROUNDING_RULES}

A UK healthcare cover letter should:
- Open with a formal greeting and state the specific role and employer being applied for.
- Briefly explain why the candidate is a strong fit, referencing specific relevant skills or experience.
- Close with a professional sign-off expressing interest in an interview.

Write 3-4 paragraphs, around 250-350 words, in formal letter format (e.g. "Dear Hiring Manager," ... "Yours faithfully,")."""


def build_cover_letter_user_prompt(
    profile: CandidateProfile, job: JobSnapshot, match_result: MatchResult | None = None
) -> str:
    parts = [
        "Candidate profile:",
        _render_profile(profile),
        "",
        "Job listing:",
        _render_job(job),
    ]
    match_text = _render_match(match_result)
    if match_text:
        parts += ["", match_text]
    parts += ["", "Write the cover letter now."]
    return "\n".join(parts)


# --- Application answer ---------------------------------------------------------


def build_application_answer_system_prompt() -> str:
    return f"""You are an expert UK healthcare careers advisor helping a candidate answer a short application question for a specific job.

{_GROUNDING_RULES}

Write a concise, focused answer that directly addresses the question asked, in plain prose (no bullet points, do not restate the question). Stay within the requested word limit."""


def build_application_answer_user_prompt(
    profile: CandidateProfile,
    job: JobSnapshot,
    question: str,
    match_result: MatchResult | None = None,
    *,
    max_words: int = 150,
) -> str:
    parts = [
        "Candidate profile:",
        _render_profile(profile),
        "",
        "Job listing:",
        _render_job(job),
    ]
    match_text = _render_match(match_result)
    if match_text:
        parts += ["", match_text]
    parts += ["", f"Application question: {question}", f"Answer in no more than {max_words} words."]
    return "\n".join(parts)


# --- Interview preparation (Anthropic AI Integration milestone) ----------------


def build_interview_prep_system_prompt() -> str:
    return f"""You are an expert UK healthcare careers advisor helping a candidate prepare for an upcoming job interview.

{_GROUNDING_RULES}

Produce interview preparation notes with these sections, using Markdown headings (##):
## Likely questions — 5-8 questions this interview panel is likely to ask, grounded in the role's requirements and the candidate's background.
## Talking points — specific examples from the candidate's actual experience/skills/certificates they should be ready to raise, and which question each best answers.
## Questions to ask them — 3-4 thoughtful questions the candidate could ask the panel about the role, team, or organisation.
## Practical reminders — brief, generic logistical reminders (arrive early, bring ID/certificates, dress code) — keep this section short."""


def build_interview_prep_user_prompt(
    profile: CandidateProfile,
    job: JobSnapshot,
    match_result: MatchResult | None = None,
    *,
    interview_type: str | None = None,
    interview_stage: str | None = None,
) -> str:
    parts = [
        "Candidate profile:",
        _render_profile(profile),
        "",
        "Job listing:",
        _render_job(job),
    ]
    match_text = _render_match(match_result)
    if match_text:
        parts += ["", match_text]
    if interview_type or interview_stage:
        parts += [
            "",
            f"Interview format: {interview_type or 'not specified'}"
            + (f" ({interview_stage.replace('_', ' ')})" if interview_stage else ""),
        ]
    parts += ["", "Write the interview preparation notes now."]
    return "\n".join(parts)


# --- Missing-skills / gap analysis (Anthropic AI Integration milestone) --------


def build_skills_gap_system_prompt() -> str:
    return f"""You are an expert UK healthcare careers advisor analysing how well a candidate's profile matches a specific job's requirements, focused specifically on gaps.

{_GROUNDING_RULES}

Produce a gap analysis with these sections, using Markdown headings (##):
## Strong matches — skills, qualifications, or experience the candidate already has that directly meet the role's requirements.
## Gaps — requirements or preferred qualifications from the job listing that the candidate's profile does not show evidence of. Be specific about what's missing, not vague.
## Closing the gaps — for each gap identified above, one concrete, realistic suggestion (a specific course, certification, type of experience to seek, or how to reframe existing experience to address it).

If the candidate's profile already covers every requirement well, say so plainly in "Gaps" rather than inventing gaps that don't exist."""


def build_skills_gap_user_prompt(
    profile: CandidateProfile, job: JobSnapshot, match_result: MatchResult | None = None
) -> str:
    parts = [
        "Candidate profile:",
        _render_profile(profile),
        "",
        "Job listing:",
        _render_job(job),
    ]
    match_text = _render_match(match_result)
    if match_text:
        parts += ["", match_text]
    parts += ["", "Write the gap analysis now."]
    return "\n".join(parts)


# --- Career insight narrative (AI Career Assistant milestone) ------------------


def build_career_insight_system_prompt() -> str:
    return f"""You are an expert UK healthcare careers advisor giving a candidate a personalised, narrative career assessment for one specific job they are considering applying to.

{_GROUNDING_RULES}

You have already been given a rule-based score breakdown and a list of strengths/gaps for this match — do not repeat it as a bare list. Instead, write a short, warm, conversational assessment with these sections, using Markdown headings (##):
## How you match — a plain-English narrative (not bullet points) explaining what this match score really means for this candidate and this specific job, weaving in the strongest and weakest scoring areas naturally.
## Making your CV stand out — 2-4 specific, actionable suggestions for how the candidate could adjust their CV or application specifically for this job, grounded only in what's actually true about them.
## Interview readiness — an honest, encouraging assessment of how ready the candidate seems for an interview at this job, and the two or three things most worth preparing before one."""


def build_career_insight_user_prompt(
    profile: CandidateProfile, job: JobSnapshot, match_result: MatchResult | None = None
) -> str:
    parts = [
        "Candidate profile:",
        _render_profile(profile),
        "",
        "Job listing:",
        _render_job(job),
    ]
    match_text = _render_match(match_result)
    if match_text:
        parts += ["", match_text]
    parts += ["", "Write the personalised career assessment now."]
    return "\n".join(parts)
