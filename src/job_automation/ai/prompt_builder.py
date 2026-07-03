"""
Prompt construction and response parsing for the LLM semantic-analysis step.

System and user prompts are separate functions/constants deliberately —
`LLMProvider.complete()` takes them as two distinct arguments (matching how
every major provider's chat API distinguishes system instructions from the
per-request content), and keeping them separate here means either can be
edited independently: the system prompt defines the analyst's role and
output contract once; the user prompt varies per candidate/job pair.

`PROMPT_VERSION` is bumped whenever the prompt shape changes materially —
`ai.cache` includes it in the cache key so a prompt change automatically
invalidates old cached analyses instead of silently reusing answers to a
question that's no longer being asked.
"""

from __future__ import annotations

import json

from job_automation.ai.llm_provider import LLMResponseError
from job_automation.ai.matching_models import MATCH_CATEGORIES, CandidateProfile, JobSnapshot, LLMAnalysis

PROMPT_VERSION = "v1"

SYSTEM_PROMPT = """You are an expert UK healthcare recruitment analyst. You \
assess how well a candidate matches a specific job listing, drawing on your \
knowledge of NHS Agenda for Change bands, UK healthcare qualifications \
(NVQ, NMC registration, DBS checks), and typical UK employment terms.

You must respond with ONLY a single valid JSON object and nothing else — no \
markdown code fences, no commentary before or after it. The JSON object must \
have exactly this shape:

{
  "category_scores": {
    "skills": <0-100 integer>,
    "experience": <0-100 integer>,
    "qualifications": <0-100 integer>,
    "location": <0-100 integer>,
    "salary": <0-100 integer>,
    "working_pattern": <0-100 integer>,
    "visa_sponsorship": <0-100 integer>,
    "employer_quality": <0-100 integer>
  },
  "strengths": [<string>, ...],
  "weaknesses": [<string>, ...],
  "missing_requirements": [<string>, ...],
  "recommended_actions": [<string>, ...]
}

Score every category even if information is incomplete — use your best \
judgement and score conservatively (around 50) when a category can't be \
assessed from the given information, rather than omitting it."""


def build_system_prompt() -> str:
    return SYSTEM_PROMPT


def build_user_prompt(candidate: CandidateProfile, job: JobSnapshot) -> str:
    return (
        "Candidate profile:\n"
        f"{candidate.to_prompt_text()}\n\n"
        "Job listing:\n"
        f"{job.to_prompt_text()}\n\n"
        "Assess this candidate against this job and respond with the JSON "
        "object described in your instructions."
    )


def parse_response(raw: str) -> LLMAnalysis:
    """Parse the LLM's raw text into an `LLMAnalysis`. Raises
    `LLMResponseError` (not transient — retrying an identical prompt won't
    fix malformed JSON) if the response isn't the expected shape."""
    text = raw.strip()
    # Tolerate a model wrapping the JSON in a markdown code fence despite
    # being told not to — strip it rather than failing outright.
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LLMResponseError(f"LLM response was not valid JSON: {exc}") from exc

    if not isinstance(data, dict) or "category_scores" not in data:
        raise LLMResponseError("LLM response JSON is missing 'category_scores'")

    category_scores = data["category_scores"]
    missing = [c for c in MATCH_CATEGORIES if c not in category_scores]
    if missing:
        raise LLMResponseError(f"LLM response is missing category scores for: {missing}")

    try:
        normalized_scores = {c: float(category_scores[c]) for c in MATCH_CATEGORIES}
    except (TypeError, ValueError) as exc:
        raise LLMResponseError(f"LLM response category scores are not numeric: {exc}") from exc

    return LLMAnalysis(
        category_scores=normalized_scores,
        strengths=list(data.get("strengths", [])),
        weaknesses=list(data.get("weaknesses", [])),
        missing_requirements=list(data.get("missing_requirements", [])),
        recommended_actions=list(data.get("recommended_actions", [])),
    )
