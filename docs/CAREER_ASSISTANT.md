# AI Career Assistant

Turns an already-computed job match into plain-English, actionable
insight: what the score really means, what's strong, what's missing, how
to improve the CV for this specific job, and a predicted interview
readiness — all shown on the job detail page, right below the existing
AI match card.

## Design: two tiers, same convention as the rest of this app

This mirrors the "rule-based always-on, real LLM call explicit-and-
optional" split already used everywhere else in this codebase — AI
matching itself (auto-runs on import; "Re-run with AI" is a manual
upgrade), and every notification (in-app is free; email is a separate,
user-controlled decision):

1. **`career_assistant.CareerAssistantService`** — pure Python, zero
   dependencies, zero LLM calls, zero database access. Takes a
   `MatchResult` (the AI matching engine's already-computed output — see
   `ai.matching_models`) and a `JobSnapshot`, and derives:
   - **A plain-English score explanation** — which categories are
     strong/weak and what that means for this specific job, not just a
     bare percentage.
   - **Strengths and missing skills** — reused directly from the match
     (`MatchResult.strengths`/`.missing_requirements`), surfaced clearly
     rather than buried in a JSON blob.
   - **Prioritized CV suggestions** — missing requirements become
     "high priority" suggestions, weaknesses become "medium," and
     already-computed recommended actions become "low" — reframing data
     the matching engine already produced into something actionable,
     capped at 5 so it stays readable.
   - **Predicted interview readiness** — a 0-100 score (60% overall match
     + 40% match confidence, minus a penalty for each unaddressed missing
     requirement) mapped to one of four levels (Interview ready / Almost
     ready / Needs preparation / Significant preparation needed), with a
     one-sentence reasoning and up to 3 focus areas.

   Because this needs nothing but data the matching engine already
   computed, it runs on **every** job detail page view where a match
   exists — instantaneous, free, and correct even with no LLM configured
   at all.

2. **`documents.career_insight_generator.CareerInsightGenerator`** — the
   optional, explicit-click, real-LLM-backed narrative companion. Wired
   into the pre-existing document-generation pipeline as a sixth
   `DocumentType` (`CAREER_INSIGHT`), reusing 100% of the same
   generate → validate → draft/needs_review → approve/reject → export
   machinery `INTERVIEW_PREP`/`SKILLS_GAP_ANALYSIS` already use — no new
   persistence, review UI, or export logic was needed. A candidate who
   wants a deeper, personalised narrative (rather than the rule-based
   version's templated sentences) clicks "Get AI-personalised insight,"
   the same "real spend requires an explicit click" invariant every
   other AI feature in this app follows.

## What's new vs. reused

- **New**: `job_automation/career_assistant/` package
  (`career_assistant_models.py`, `career_assistant_service.py`),
  `DocumentType.CAREER_INSIGHT`, `documents/career_insight_generator.py`,
  `build_career_insight_system_prompt()`/`_user_prompt()` in
  `documents/prompt_builder.py`, `components/career_assistant_panel.html`.
- **Reused, unchanged**: `ai.matching_models.MatchResult`/`JobSnapshot`
  (the entire input), `DocumentService`'s generate/validate/persist
  pipeline, `AIResponseCache` (the same on-disk cache every other
  generator uses), the existing "AI match" card and document-generation
  form on the job detail page.
- **No new migration** — `DocumentType` is stored as a plain string
  column (`GeneratedDocumentRecord.document_type`), so adding a new enum
  value needed zero schema changes; `career_assistant`'s own models are
  plain dataclasses, never persisted.

## Where it shows up

`routes/jobs.py`'s `job_detail()` route computes a `CareerInsight` (via
`CareerAssistantService.build_insight()`) whenever a `JobMatch` with
analysis exists, and passes it to the template as `career_insight` —
purely additive to the existing context dict. `job_detail.html` renders
`components/career_assistant_panel.html` right after the existing AI
match card, only when `career_insight` is not `None` (an unmatched job
shows no panel at all, not an empty one). The panel includes two buttons:
"Generate interview prep" (already existed; now more discoverable here)
and "Get AI-personalised insight" (new), both posting to the existing
`/jobs/{id}/documents/generate` route with no changes to that route's
existing behavior for any other document type.

## No breaking changes

- Every existing route, template, and page continues to behave exactly
  as before for any request that doesn't touch career insight.
- `job_detail()`'s only change is one new, `None`-able context key.
- `DocumentService`'s constructor gained one new optional keyword
  argument (`career_insight_generator`) with a default, so every existing
  call site is unaffected.
- `routes/documents.py`'s `generate_document()` dispatch gained one new
  `elif` branch; every existing branch is untouched.
- Full test suite: 494 passed (468 pre-existing + 26 new), zero
  regressions.

## Testing

`tests/test_career_assistant.py` — 23 tests:

- **Category insights**: score-bucket boundaries (80/60/40 thresholds);
  display order always follows `ai.matching_models.MATCH_CATEGORIES`
  regardless of the input dict's own ordering; only categories actually
  present in the match are included.
- **Summary**: mentions job title, employer, and overall score; handles
  a job with no employer set without a literal "at None"; calls out both
  strong and weak categories by name.
- **CV suggestions**: missing requirements always rank "high" priority
  first; an empty suggestion list when the match has no gaps at all;
  capped at 5 even with many inputs.
- **Interview readiness**: a high score with no gaps reaches "Interview
  ready"; a low score with many gaps floors at 0 and reaches
  "Significant preparation needed" (never negative); focus areas prefer
  missing requirements, falling back to weaknesses when there are none;
  the reasoning sentence reads naturally and includes the real numbers.
- **`CareerInsightGenerator`**: returns a `CAREER_INSIGHT` document with
  the grounding rules and full candidate/job context in its prompts — no
  real LLM call (`FakeLLMProvider`, the same pattern as every other
  generator's tests).
- **`DocumentService.generate_career_insight()`**: end-to-end wiring
  through validation and persistence.

`tests/test_web_dashboard.py` — 3 new tests: the panel renders with the
plain-English summary/interview-readiness/CV-suggestions sections and no
raw Jinja when a match exists; the panel is entirely absent for an
unmatched job; `POST /jobs/{id}/documents/generate` with
`document_type=career_insight` creates a draft and redirects, same shape
as every pre-existing document type.

## Known limitations

- **No caching of the deterministic insight** — `build_insight()` is
  cheap enough (pure Python, no I/O) that recomputing it on every page
  view was a deliberate choice over adding a cache layer for something
  this fast.
- **The AI-enhanced narrative isn't automatically re-shown** — like every
  other document type, generating a new `CAREER_INSIGHT` document creates
  a new draft rather than updating an existing one in place; the
  candidate finds it via the existing Documents page/list, the same way
  every other generated document already works.
- **Interview readiness is a heuristic, not a validated model** — the
  weights (60% overall / 40% confidence, an 8-point penalty per missing
  requirement) are a reasonable starting point, not empirically tuned
  against real interview outcomes.
