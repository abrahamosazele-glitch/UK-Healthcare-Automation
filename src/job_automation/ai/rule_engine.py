"""
Deterministic, non-LLM scoring for all 8 match categories.

This exists as a first-class, independent scoring path — not just an LLM
fallback — for three reasons: it's free and instant (no API call), it's
fully deterministic (same inputs always produce the same score, useful for
testing and for explaining a result to a user), and it still works when no
LLM provider is configured at all. `ScoreCalculator` blends this with the
LLM's semantic assessment when one is available, and falls back to this
alone when it isn't.

Matching here is intentionally simple (substring/keyword overlap) rather
than any embedding-based semantic similarity — that's exactly the gap the
LLM step is meant to fill (a candidate whose skills are phrased differently
from a job's requirements but mean the same thing is something an LLM can
recognize and a substring match cannot).
"""

from __future__ import annotations

from job_automation.ai.matching_models import CandidateProfile, JobSnapshot


class RuleEngine:
    def score(self, candidate: CandidateProfile, job: JobSnapshot) -> dict[str, float]:
        return {
            "skills": self._score_skills(candidate, job),
            "experience": self._score_experience(candidate, job),
            "qualifications": self._score_qualifications(candidate, job),
            "location": self._score_location(candidate, job),
            "salary": self._score_salary(candidate, job),
            "working_pattern": self._score_working_pattern(candidate, job),
            "visa_sponsorship": self._score_visa_sponsorship(candidate, job),
            "employer_quality": self._score_employer_quality(candidate, job),
        }

    def matched_keywords(self, candidate: CandidateProfile, job: JobSnapshot) -> list[str]:
        """Which of the candidate's skills/keywords actually appear in the
        job's text — persisted as `JobMatch.matched_keywords`."""
        job_text = self._job_text(job)
        seen: dict[str, str] = {}
        for term in (*candidate.skills, *candidate.keywords):
            if term.lower() in job_text:
                seen[term.lower()] = term
        return list(seen.values())

    def _score_skills(self, candidate: CandidateProfile, job: JobSnapshot) -> float:
        terms = {t.lower() for t in (*candidate.skills, *candidate.keywords)}
        if not terms:
            return 50.0  # no data to compare — neutral, not a penalty
        job_text = self._job_text(job)
        matched = sum(1 for term in terms if term in job_text)
        return round(100 * matched / len(terms), 1)

    def _score_experience(self, candidate: CandidateProfile, job: JobSnapshot) -> float:
        if not candidate.experience:
            return 50.0
        job_title_words = set(job.title.lower().split())
        best_overlap = 0.0
        for entry in candidate.experience:
            entry_words = set(entry.job_title.lower().split())
            if not entry_words:
                continue
            overlap = len(entry_words & job_title_words) / len(entry_words)
            best_overlap = max(best_overlap, overlap)
        # Baseline 50 (candidate has *some* experience) scaled up to 100 for
        # a strong job-title word overlap with a past role.
        return round(50 + best_overlap * 50, 1)

    def _score_qualifications(self, candidate: CandidateProfile, job: JobSnapshot) -> float:
        candidate_quals = {c.lower() for c in candidate.certificates}
        candidate_quals |= {e.qualification.lower() for e in candidate.education}
        if not candidate_quals:
            return 50.0
        requirements_text = " ".join(job.requirements).lower()
        if not requirements_text:
            return 50.0
        matched = sum(
            1 for qual in candidate_quals if qual in requirements_text or requirements_text in qual
        )
        return round(100 * matched / len(candidate_quals), 1)

    def _score_location(self, candidate: CandidateProfile, job: JobSnapshot) -> float:
        if not candidate.preferred_locations:
            return 100.0  # no preference expressed = fully compatible
        if not job.location:
            return 50.0  # unknown — neutral, not a penalty
        job_location = job.location.lower()
        for preferred in candidate.preferred_locations:
            preferred_lower = preferred.lower()
            if preferred_lower in job_location or job_location in preferred_lower:
                return 100.0
        return 20.0

    def _score_salary(self, candidate: CandidateProfile, job: JobSnapshot) -> float:
        if candidate.preferred_salary_min is None:
            return 100.0
        job_salary = job.salary_max if job.salary_max is not None else job.salary_min
        if job_salary is None:
            return 50.0
        if job_salary >= candidate.preferred_salary_min:
            return 100.0
        return round(max(0.0, 100 * job_salary / candidate.preferred_salary_min), 1)

    def _score_working_pattern(self, candidate: CandidateProfile, job: JobSnapshot) -> float:
        if not candidate.working_pattern_preference:
            return 100.0
        if not job.working_pattern:
            return 50.0
        preferred = candidate.working_pattern_preference.lower()
        actual = job.working_pattern.lower()
        return 100.0 if preferred in actual or actual in preferred else 30.0

    def _score_visa_sponsorship(self, candidate: CandidateProfile, job: JobSnapshot) -> float:
        if not candidate.visa_sponsorship_required:
            return 100.0  # not needed — this category is a non-issue
        if job.visa_sponsorship is None:
            return 50.0  # unknown whether the job offers it
        return 100.0 if job.visa_sponsorship else 0.0

    def _score_employer_quality(self, candidate: CandidateProfile, job: JobSnapshot) -> float:
        # No external employer-ratings data source is integrated (known
        # limitation — see docs/AI_MATCHING.md). This heuristic only
        # distinguishes "recognizably an NHS body" from "unknown", which is
        # a real (if weak) signal — not a random/placeholder number.
        if not job.employer:
            return 50.0
        name = job.employer.lower()
        if "nhs" in name or "trust" in name:
            return 80.0
        return 60.0

    @staticmethod
    def _job_text(job: JobSnapshot) -> str:
        parts = [job.title, job.description or "", " ".join(job.requirements), " ".join(job.benefits)]
        return " ".join(parts).lower()
