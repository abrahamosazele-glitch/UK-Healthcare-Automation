# AI Matching Engine

`src/job_automation/ai/` evaluates jobs already stored in the database
against a candidate profile and produces a structured match assessment. It
does not scrape anything, and it does not submit applications — it only
scores jobs that some other part of the system (currently the NHS Jobs
scraper) has already saved.

## Architecture

```
src/job_automation/ai/
├── matching_models.py    — CandidateProfile, JobSnapshot, LLMAnalysis, MatchResult (pure dataclasses)
├── llm_provider.py        — LLMProvider ABC + exception hierarchy
├── anthropic_provider.py  — AnthropicProvider(LLMProvider) — first concrete implementation
├── prompt_builder.py      — system/user prompt construction + response parsing
├── rule_engine.py         — deterministic, non-LLM scoring for all 8 categories
├── score_calculator.py    — blends rule + LLM scores into one MatchResult
├── cache.py                — MatchCache — avoids repeated LLM calls
├── matching_engine.py     — orchestrates the above into evaluate()
├── profile_builder.py     — builds CandidateProfile from data/candidate_profile.json (+ optional DB enrichment)
└── matching_service.py    — DB integration: evaluates stored Jobs, persists JobMatch rows
```

Dependency direction: `matching_models` has no dependencies on anything else
in this package (everything else depends on it). `matching_engine` depends
on `rule_engine`, `score_calculator`, `llm_provider`, `cache`, and
`prompt_builder` — but never on a *concrete* provider (`anthropic_provider`)
or on the database. `matching_service` is the only file that imports both
`job_automation.ai` and `job_automation.database` — it's the intentional
bridge between the two, mirroring how `job_automation.database.services
.job_ingestion_service` bridges the scraper framework and the database.

```
matching_service.py  ──┐
                        ├──> matching_engine.py ──┬──> rule_engine.py
database/repositories ──┘                          ├──> score_calculator.py
                                                     ├──> cache.py
                                                     └──> llm_provider.py (ABC)
                                                              ▲
                                                      anthropic_provider.py
```

### Why a `CandidateProfile` value object instead of a new database table

The candidate's rich preference data (skills, experience, education,
preferred locations/salary/band/working pattern, keywords) has no schema of
its own — the `User` model only holds account/contact fields. Rather than
add a large new table for what is, in this project, a single-user personal
automation tool, `profile_builder.py` builds a `CandidateProfile` from
`data/candidate_profile.json` (scaffolded in the very first project
milestone for CV/cover-letter generation, and extended now with the new
preference fields this milestone needs — see
`data/candidate_profile.example.json`). It's optionally enriched with
`Certificate` rows from the database when a session and user_id are
supplied, so certificates added via the database aren't missed.

### Why `Job.visa_sponsorship` and `JobMatch.analysis` were added

Two small, necessary schema extensions (migration
`e167f5bf7f2e_add_visa_sponsorship_to_job_and_...`):

- **`Job.visa_sponsorship`** (nullable boolean) — the "visa sponsorship"
  scoring category needs a job-side signal to compare the candidate's
  preference against. `ParsedJob` (the scraper framework's generic output
  type) has carried this field since the very first scraper-framework
  milestone, but NHS's own required field list never included it, so it was
  never wired to a column. `None` means "unknown," not "no."
- **`JobMatch.analysis`** (JSON) — the rich per-category breakdown
  (confidence, strengths, weaknesses, missing requirements, recommended
  actions, all 8 category scores) is naturally nested, and the exact
  category set may evolve — one JSON column is more appropriate than ~8
  additional scalar columns. `JobMatch.match_score` (already existed)
  remains the single overall 0-100 figure for fast querying/sorting.

## Candidate profile

Built by `profile_builder.build_candidate_profile()`:

| Field | Source |
|---|---|
| `skills` | `candidate_profile.json`: `skills` |
| `experience` | `work_experience` (job title, employer, responsibilities) |
| `education` | `qualifications` (name, awarding body, year) |
| `certificates` | `certifications`, merged with any DB `Certificate` rows for the user |
| `preferred_locations` | `preferred_locations` (new field) |
| `preferred_salary_min` | `preferred_salary_min` (new field) |
| `preferred_band` | `preferred_band` (new field) |
| `visa_sponsorship_required` | `visa_sponsorship_required` if set, else inferred as `not right_to_work_uk` |
| `working_pattern_preference` | `working_pattern_preference` (new field) |
| `keywords` | `keywords` (new field) |

## Matching pipeline

```
MatchingEngine.evaluate(candidate, job)
    │
    ├─ RuleEngine.score(candidate, job)              -> dict[category, 0-100]  (always runs, free, deterministic)
    ├─ RuleEngine.matched_keywords(candidate, job)    -> list[str]
    │
    ├─ if an LLMProvider is configured:
    │     MatchCache.compute_key(candidate, job)      -> sha256(candidate.fingerprint() | job.content_fingerprint() | PROMPT_VERSION)
    │     cache hit?  -> reuse cached LLMAnalysis, skip the API call entirely
    │     cache miss? -> prompt_builder.build_system_prompt() + build_user_prompt()
    │                    -> LLMProvider.complete(...)
    │                    -> prompt_builder.parse_response(...)  -> LLMAnalysis
    │                    -> MatchCache.set(key, analysis)
    │     LLM call failed? -> log it, continue with llm_analysis=None (never crash the evaluation)
    │
    └─ ScoreCalculator.calculate(rule_scores, llm_analysis, matched_keywords)  -> MatchResult
```

## Scoring algorithm

**Per category** (skills, experience, qualifications, location, salary,
working_pattern, visa_sponsorship, employer_quality): if an LLM analysis is
available, the combined score is `rule_score * 0.4 + llm_score * 0.6`; the
LLM is weighted higher since it can recognize equivalent skills phrased
differently (which substring matching cannot), but the rule score still
anchors the result so one LLM outlier doesn't swing it entirely. Without an
LLM, the category score is 100% rule-based.

**Overall score**: a weighted average of the 8 (now-combined) category
scores:

| Category | Weight | Why |
|---|---|---|
| Skills | 25% | Strongest predictor of genuine fit |
| Experience | 15% | |
| Qualifications | 10% | |
| Location | 15% | |
| Salary | 15% | |
| Working pattern | 10% | |
| Visa sponsorship | 5% | Binary/coarse signal, not nuanced |
| Employer quality | 5% | Weak heuristic (see Known limitations) |

**Confidence score**: reflects how much the assessment can be trusted, not
how good the match is. Rule-only results are capped at 55 (substring
matching alone is weak evidence). With an LLM, confidence starts at 95 and
is reduced by the average per-category disagreement between the rule and
LLM scores — large disagreement suggests at least one signal is unreliable
for this particular pair, floored at 40.

**Rule-engine category logic** (`rule_engine.py`) — all deterministic,
all documented per-method in code:
- *Skills*: fraction of candidate skills/keywords found in the job's
  title+description+requirements+benefits text.
- *Experience*: baseline 50 (any experience at all) scaled toward 100 by
  word overlap between a past job title and this job's title.
- *Qualifications*: fraction of candidate certificates/education entries
  found in (or containing) the job's requirements text.
- *Location*: 100 if no preference or the location matches; 50 if the job's
  location is unknown; 20 otherwise.
- *Salary*: 100 if the job meets or exceeds the candidate's minimum; scaled
  proportionally down otherwise; 50 if unknown; 100 if no preference.
- *Working pattern*: 100 for a match or no preference; 50 if unknown; 30 for
  a clear mismatch.
- *Visa sponsorship*: 100 if not required by the candidate (a non-issue);
  otherwise 100/0/50 for yes/no/unknown from the job.
- *Employer quality*: 80 if the employer name looks like an NHS body
  ("nhs"/"trust" substring), else 60 — see Known limitations.

**Output** (`MatchResult`): `overall_score`, `confidence_score`,
`category_scores`, `matched_keywords`, `strengths`, `weaknesses`,
`missing_requirements`, `recommended_actions`, `used_llm`. When no LLM ran,
`strengths`/`weaknesses` are derived from rule-score thresholds (≥75 /
≤40) and `recommended_actions` gives a generic review prompt —
`missing_requirements` is only ever populated by the LLM (the rule engine
has no way to identify a *specific* missing requirement, only a low
qualifications score).

## Prompt strategy

System and user prompts are separate functions (`build_system_prompt()`,
`build_user_prompt()`) so `LLMProvider.complete()` always receives them as
distinct arguments, matching how every major chat-completion API
distinguishes role instructions from per-request content. The system prompt
fixes the analyst persona and the exact JSON output contract once; the user
prompt only ever varies with the candidate/job pair, making both trivially
reusable and independently editable.

The system prompt asks for **only** a JSON object matching an exact schema
(all 8 category scores plus the four list fields), and `parse_response()`
validates that shape strictly — raising `LLMResponseError` (not transient;
retrying an identical malformed response won't help) if any category is
missing or non-numeric. It tolerates a model wrapping the JSON in a markdown
code fence despite being told not to, since that's a common, harmless
deviation worth shrugging off rather than failing on.

`PROMPT_VERSION` ("v1") is included in the cache key — bumping it when the
prompt shape changes materially invalidates old cached analyses
automatically, rather than silently reusing answers to a question that's no
longer being asked.

## LLM provider abstraction

`LLMProvider` is one abstract method: `complete(system_prompt, user_prompt,
max_tokens) -> str`. `MatchingEngine` only ever calls this method — it never
imports a provider SDK or knows a response's native shape. `AnthropicProvider`
is the first (only) implementation, wrapping the `anthropic` SDK and
translating its exceptions to `LLMTransientError` (rate limits, timeouts,
connection errors — reuses `job_automation.core.browser_exceptions
.TransientError` as the retryability marker, the same one `core`'s
`RetryManager` already uses for browser failures) or a plain
`LLMProviderError` otherwise. Retries are handled by
`job_automation.core.retry_manager.RetryManager` — the same class the
browser framework uses — not a second bespoke retry loop.

**Adding a second provider later** (e.g. `OpenAIProvider`) requires:
1. Implement `LLMProvider.complete()` against the new SDK in a new file
   (`openai_provider.py`).
2. Translate that SDK's transient exceptions to `LLMTransientError`.
3. Inject the new provider wherever `AnthropicProvider` is injected today.

Nothing in `matching_engine.py`, `rule_engine.py`, `score_calculator.py`, or
`prompt_builder.py` changes.

## Caching

`MatchCache` caches the *LLM analysis* specifically, not the whole pipeline
result — rule-based scores are free and deterministic, so caching them adds
complexity for no benefit; a rule-engine code change also takes effect
immediately on the next run this way, with no cache to invalidate.

Backed by JSON files under `data/cache/ai_matches/` (not an in-memory dict)
specifically because "avoid repeated LLM calls" implies persistence across
process runs, not just within one. Cache key: `sha256(candidate.fingerprint()
| job.content_fingerprint() | PROMPT_VERSION)`. `CandidateProfile
.fingerprint()` hashes the profile's full prompt text (any preference change
invalidates the cache for every job); `JobSnapshot.content_fingerprint()`
prefers the job's existing `content_hash` (already computed by
`JobIngestionService`) and falls back to hashing its own prompt text when
none is set (e.g. in tests). Verified directly:
`test_matching_engine_caches_llm_analysis_to_avoid_repeated_calls` confirms
a second `evaluate()` call for the same pair makes zero additional provider
calls, and `test_matching_engine_cache_misses_for_a_different_job` confirms
a different job's content hash correctly produces a cache miss.

## Extension points

- **New LLM providers** — implement `LLMProvider`, see above.
- **New scoring categories** — add to `MATCH_CATEGORIES` in
  `matching_models.py`, add a `_score_*` method to `RuleEngine`, add a
  weight to `CATEGORY_WEIGHTS` in `score_calculator.py` (re-normalize so
  they still sum to 1.0), and add the category to the JSON schema in
  `prompt_builder.SYSTEM_PROMPT`.
- **New candidate profile sources** — `profile_builder.py` is the only file
  that knows about the JSON file format; a future web UI or API could build
  a `CandidateProfile` a different way entirely without touching the engine.
- **Persistence beyond `JobMatch`** — `matching_service.py` is the only file
  that knows about `JobMatch`; a different consumer (e.g. a future report
  generator) can call `MatchingEngine.evaluate()` directly and do something
  else with the `MatchResult` without touching the database layer at all.

## Known limitations

- **Employer quality has no real data source.** The heuristic ("nhs"/"trust"
  substring in the employer name) is a genuine, deterministic rule, but a
  weak one — there's no external ratings/reviews integration.
- **Rule-based skill/qualification matching is substring-based, not
  semantic.** This is precisely the gap the LLM step exists to fill; without
  an LLM provider configured, a candidate whose skills are phrased
  differently from a job's requirements will score lower than they should.
- **No LLM call has been made against the real Anthropic API in this
  milestone** — no API key is configured in this environment. Every LLM-path
  test uses `FakeLLMProvider`, a minimal in-test double. `AnthropicProvider`
  is complete, production code, but its actual network behavior against the
  live API is unverified here.
- **Distance-based location scoring isn't implemented** — matching is
  substring-based ("London" in "Central London"), not geographic radius.
- **No batch/async LLM calls** — `evaluate_active_jobs()` calls the provider
  once per job, sequentially; for a large job set this could be slow and
  costly without the cache.

## Future improvements

- Verify `AnthropicProvider` against the real API once a key is available,
  and tune `max_tokens`/model choice based on real response quality.
- Add a second provider (OpenAI) to prove the abstraction in practice, not
  just in principle.
- Batch multiple jobs into fewer LLM calls where the model's context window
  allows it, to reduce cost for `evaluate_active_jobs()`.
- Replace the employer-quality heuristic with a real data source (e.g. CQC
  ratings for care providers) if one becomes available.
- Surface `MatchResult` to the user (a report or UI) — out of scope for this
  milestone, which only builds the engine and its persistence.

## Testing

`tests/test_ai_matching.py` — 22 tests, all passing, no real LLM calls made:

- **Rule engine**: every category scores something sensible with no data
  (never 0), rewards real overlap, penalizes real mismatches, and visa
  sponsorship scoring is verified for all three candidate/job combinations.
- **Prompt builder**: system and user prompts are genuinely different text;
  valid responses parse correctly; a markdown-fenced response is tolerated;
  malformed/incomplete responses raise `LLMResponseError`.
- **Score calculator**: rule+LLM blending produces the expected weighted
  value; rule-only fallback produces a complete result with generated
  strengths/weaknesses and capped confidence; confidence drops measurably
  when rule and LLM scores disagree.
- **Provider abstraction**: `AnthropicProvider` is an `LLMProvider` and
  validates its API key at construction; `MatchingEngine` is proven to only
  depend on the abstract interface via `FakeLLMProvider`.
- **Caching**: a second evaluation of the same (candidate, job) pair makes
  zero additional provider calls; a different job produces a cache miss.
  Graceful fallback when the provider raises is also verified.
- **Persistence**: `MatchingService` inserts a new `JobMatch`, then updates
  the same row (never duplicates) on re-evaluation; only active jobs are
  evaluated by `evaluate_active_jobs()`.
- **Profile builder**: reads the JSON file correctly, infers
  `visa_sponsorship_required` from `right_to_work_uk` when not explicitly
  set (and respects an explicit value when given), and merges in
  database-stored `Certificate` rows.
