# Anthropic AI Integration

Replaces the rule-based/fake-only AI path with a real `AnthropicProvider`
for five generation features, while keeping every test on `FakeLLMProvider`
— no test in this suite ever makes a real Anthropic API call. **No
deployment, no live scraping, no automatic application submission.**

## What changed

Before this milestone, `AnthropicProvider` and the `LLMProvider` interface
already existed (built for the AI matching engine), but nothing in the
dashboard actually used a real key end-to-end: matching always ran through
`SchedulerFakeLLMProvider` (background scheduler), and only one route
(`/documents/{id}/regenerate`) could ever call `AnthropicProvider` at all.
"Interview preparation" and "missing skills analysis" didn't exist as
features. This milestone:

1. Wired real AI provider configuration into `settings` (`.env`-only).
2. Enhanced `AnthropicProvider` with configurable timeout, retry, and
   cost/token logging.
3. Added a generic on-disk response cache (`AIResponseCache`) shared by
   every text-generation feature.
4. Added two new document types — interview preparation and skills-gap
   analysis — reusing the entire existing `DocumentService` pipeline.
5. Added manual, explicit-click triggers for all five AI features: job
   match re-analysis, supporting statement, cover letter, application
   answer, interview prep, skills-gap analysis. **Nothing runs
   automatically** — every real AI call in the web dashboard originates
   from a user clicking a button.
6. Added an AI status indicator to the dashboard.

## Configuration

Read only from `.env`/the process environment (`config/settings.py`),
**never** the database, **never** exposed in any template or API response:

```
ANTHROPIC_API_KEY=                          # blank = AI features disabled, not crashed
ANTHROPIC_MODEL=claude-sonnet-4-6
ANTHROPIC_TIMEOUT_SECONDS=60.0
ANTHROPIC_MAX_RETRIES=3
ANTHROPIC_INPUT_COST_PER_MILLION_USD=3.0    # for the cost estimate in logs only
ANTHROPIC_OUTPUT_COST_PER_MILLION_USD=15.0
```

`.env` is already listed in `.gitignore` (confirmed, not re-added); `
.env.example` documents every field above with a comment, but ships with
`ANTHROPIC_API_KEY=` blank — this repository never contains a real key.

With no key configured, every AI feature fails closed with a clear `503`
(`AnthropicProvider requires an API key ... Set ANTHROPIC_API_KEY in your
.env file — see .env.example.`), not a silent fallback or a crash. The
dashboard's AI status widget shows this state plainly rather than a user
discovering it by clicking a broken button.

## `AnthropicProvider` (`ai/anthropic_provider.py`)

- **Timeout** — passed straight to the SDK's `httpx` client
  (`anthropic.Anthropic(..., timeout=settings.anthropic_timeout_seconds)`);
  not reimplemented, since the SDK already raises `APITimeoutError` when it
  fires.
- **Retry** — reuses `core.retry_manager.RetryManager` (the same class
  `BrowserManager`/`PageManager` use), not a second bespoke retry loop.
  `anthropic.RateLimitError`/`APITimeoutError`/`APIConnectionError` are
  translated to `LLMTransientError` at the point of contact (which is both
  an `LLMProviderError` and a `TransientError`), so `RetryManager` retries
  them with exponential backoff + jitter; any other `anthropic.APIError`
  becomes a plain (non-retried) `LLMProviderError`.
- **Cost/token logging** — every real completion logs the model,
  input/output token counts, and an estimated USD cost via `logger.info`
  (loguru, `logs/app.log`). This is a log line, not a new database table —
  an operational/debugging concern, not analytics data a page queries.
  Estimates use `settings.anthropic_*_cost_per_million_usd`, which are
  approximate published rates, not billing-accurate figures.
- **Missing API key** raises `LLMProviderError` immediately, in the
  constructor, before any SDK client is even created — callers get one
  consistent, actionable error message everywhere (`get_llm_provider()`
  turns it into a `503` for web routes).

## Caching

Two separate on-disk caches, both under `data/cache/`, both JSON/text
files keyed by a SHA-256 hash — deliberately not unified, because they
cache different shapes for different reasons:

- **`MatchCache`** (`data/cache/ai_matches/`, pre-existing) — caches
  structured `LLMAnalysis` for job matching, keyed on
  `candidate.fingerprint() | job.content_fingerprint() | PROMPT_VERSION`.
- **`AIResponseCache`** (`data/cache/ai_responses/`, new this milestone)
  — caches plain generated text for all five document-generation features,
  keyed on a hash of `(kind, system_prompt, user_prompt)` directly. The
  richer `profile.candidate_profile.CandidateProfile` used by document
  generation has no `.fingerprint()` method (unlike the simpler
  `ai.matching_models.CandidateProfile`), and the fully-rendered user
  prompt already encodes the profile, job, match context, and any extra
  input (a question, an interview type) — hashing it directly is simpler
  and exactly as correct as separately fingerprinting its ingredients.

`ai.cache.complete_with_cache(llm_provider, cache, *, kind, system_prompt,
user_prompt, max_tokens)` is the shared cache-check/call/cache-set wrapper
all five generators call instead of `llm_provider.complete()` directly.
`cache=None` (every generator's default) makes it behave exactly like
calling `.complete()` directly — caching is strictly additive, never a
behavior change for a caller that doesn't pass one. Both caches are shared
app-wide singletons (`web.app.match_cache` / `web.app.ai_response_cache`,
exposed via `get_match_cache()`/`get_ai_response_cache()` FastAPI
dependencies) so a second identical request across different routes still
gets a cache hit, and so tests can override them with a `tmp_path`-backed
instance rather than touching the real project cache directory.

## The five AI features

All five ultimately call `LLMProvider.complete()` through
`complete_with_cache()`. The first three reuse the exact
generate-validate-save pipeline the Document Generation Intelligence
milestone already built; the last two are new `DocumentType` values added
to that same pipeline rather than new standalone services.

| Feature | Generator | Trigger |
|---|---|---|
| Job match re-analysis | `MatchingEngine` (existing) | `POST /matches/{job_id}/rematch` |
| Supporting statement | `SupportingStatementGenerator` (existing) | `POST /jobs/{job_id}/documents/generate` |
| Cover letter | `CoverLetterGenerator` (existing) | `POST /jobs/{job_id}/documents/generate` |
| Application answer | `ApplicationAnswerGenerator` (existing) | `POST /jobs/{job_id}/documents/generate` |
| Interview preparation | `InterviewPrepGenerator` (new) | `POST /interviews/{id}/generate-prep` |
| Missing-skills / gap analysis | `SkillsGapGenerator` (new) | `POST /jobs/{job_id}/documents/generate` |

- **`DocumentType.INTERVIEW_PREP`/`SKILLS_GAP_ANALYSIS`** — two new plain-
  string enum values on the existing `document_type` column (`String(30)`,
  no migration needed). `export_manager.py` and `document_review.html` are
  already fully generic on `.document_type.value`, so both new types work
  in review/export/approve/reject with zero further changes.
- **`DocumentService.generate_interview_prep()`/`.generate_skills_gap_analysis()`**
  follow the identical `generate → _validate_and_save()` pattern as the
  three pre-existing `generate_*()` methods — same validation, same
  draft/needs-review status assignment, same `DOCUMENT_GENERATED` event.
- **Job match re-analysis** does not go through `DocumentService` at all —
  it reuses `MatchingEngine`/`MatchingService` directly (the same classes
  the background matching task uses), just with a real `AnthropicProvider`
  and `MatchCache` instead of `SchedulerFakeLLMProvider`. The adapter
  between the two `CandidateProfile` representations
  (`ai.profile_builder.to_ai_profile()`) is shared with
  `scheduler.tasks.run_ai_matching`, not duplicated.

## Explicit-only, never automatic

Every real-AI action in the web dashboard requires a user clicking a
button — consistent with this project's established "no automatic
workflow/application changes" invariant:

- **`/matches/{job_id}/rematch`** — shown only next to a "rule-based only"
  badge on the AI match card; redirects back to the job page.
- **`/jobs/{job_id}/documents/generate`** — a form on the job detail page
  (document type + optional question for an application answer);
  redirects to the new document's review page.
- **`/interviews/{id}/generate-prep`** — shown on the interview detail page
  only when the interview has a linked job (a `JobSnapshot` needs a job
  listing); redirects to the new document's review page.

The background scheduler tasks (`run_ai_matching`, `generate_draft_documents`)
are **unchanged** — they still always use `SchedulerFakeLLMProvider`, never
a real key, so routine background jobs never incur real API cost. Only a
user-initiated dashboard click ever calls `AnthropicProvider`.

## Dashboard AI status

The dashboard's "AI status" card (`AnalyticsService.ai_status()`) shows,
computed entirely from existing data (no new usage-tracking table):

- **Configured** (green) / **Not configured** (grey) — `bool(settings
  .anthropic_api_key)`.
- **Model** — `settings.anthropic_model`.
- **AI match coverage** — the percentage of the user's `JobMatch` rows
  whose stored `analysis["used_llm"]` is `True`, i.e. how many of their job
  matches were actually scored by a real LLM call rather than falling back
  to rule-only scoring.

## Tests

`tests/test_anthropic_provider.py` is the one place `AnthropicProvider`
itself is exercised — `anthropic.Anthropic` is patched at the class level
so every test controls `.messages.create(...)`'s return value or raised
exception directly; no network access, no real API key needed. Covers:
missing-key construction failure, successful completion + usage logging,
multi-block text concatenation, empty-content rejection, transient-error
retry-then-succeed, retry exhaustion, and non-transient errors *not* being
retried.

Every other test (`test_document_generation.py`, `test_web_dashboard.py`,
`test_ai_matching.py`) substitutes `FakeLLMProvider` at the `LLMProvider`
abstraction boundary via `app.dependency_overrides[get_llm_provider]` —
the same pattern already established before this milestone — and never
touches the `anthropic` package. `test_web_dashboard.py`'s `client` fixture
also overrides `get_ai_response_cache`/`get_match_cache` with `tmp_path`-
backed instances, so tests never read or write the real
`data/cache/` directory.

New coverage added this milestone: `test_anthropic_provider.py` (11 tests),
plus new tests in `test_document_generation.py` (interview prep / skills-gap
generators, `complete_with_cache`, `DocumentService`'s two new
`generate_*()` methods) and `test_web_dashboard.py` (the three new manual-
trigger routes, `AnalyticsService.ai_status()`, the dashboard AI status
card rendering). Full suite: 387 tests, zero real API calls.

## Manual verification

No `.env` file exists in this project yet, so no real Anthropic API call
has been made — every check above was run against `FakeLLMProvider` or a
mocked SDK client. To perform the one required real-call verification:

1. Copy `.env.example` to `.env` and set a real `ANTHROPIC_API_KEY`.
2. Start the app and log in.
3. Confirm the dashboard's AI status card reads "Connected" with the
   configured model.
4. Click a trigger — e.g. "Generate" a supporting statement from a job's
   detail page — and confirm a real completion comes back, cost/token
   usage is logged to `logs/app.log`, and the resulting document is
   reviewable at `/documents/{id}`.
5. Repeat the identical request and confirm the second call is served from
   `AIResponseCache` (no second billed call, no new log line for a fresh
   API request).

This step is deliberately left for whoever holds a real API key to run —
this environment has none.

## Out of scope (per this milestone's instructions)

No deployment. No live job scraping. No automatic application submission.
No API key storage in the database. No API key exposed in any template,
API response, or client-side code — `settings.anthropic_api_key` is read
only inside `get_llm_provider()`/`AnthropicProvider.__init__()`, server-side,
and never serialized anywhere.
