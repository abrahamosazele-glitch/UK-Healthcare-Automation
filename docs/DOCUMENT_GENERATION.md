# Document Generation Intelligence

`src/job_automation/documents/` generates reviewable **draft** application
documents — NHS-style supporting statements, cover letters, and short
application answers — by bringing together three subsystems built in
earlier milestones: `profile.CandidateProfile` (source of truth for who the
candidate is), `ai.matching_models.JobSnapshot`/`MatchResult` (what the job
is and how well it matches), and `ai.llm_provider.LLMProvider` (how text
actually gets generated). Nothing here submits, sends, or applies to
anything — every document is a draft pending human review.

## Architecture

```
src/job_automation/documents/
├── document_models.py                — DocumentType, DocumentStatus, GeneratedDocument, DocumentValidationIssue
├── prompt_builder.py                  — grounding rules + per-type system/user prompts
├── supporting_statement_generator.py  — SupportingStatementGenerator(LLMProvider)
├── cover_letter_generator.py          — CoverLetterGenerator(LLMProvider)
├── application_answer_generator.py    — ApplicationAnswerGenerator(LLMProvider)
├── document_validator.py              — DocumentValidator (unsupported-claim detection)
├── document_repository.py             — DocumentRepository (repository pattern)
├── export_manager.py                  — ExportManager (Markdown/TXT export)
└── document_service.py                — DocumentService (orchestrates everything above)
```

```
DocumentService
    ├──> {Generator}.generate(profile, job, match_result)
    │        └──> prompt_builder.build_*_system_prompt() / build_*_user_prompt()
    │        └──> LLMProvider.complete(...)                 [job_automation.ai — reused, not reimplemented]
    ├──> DocumentValidator.validate(document, profile)
    ├──> DocumentRepository.create/get/update_status(...)   [-> GeneratedDocumentRecord]
    └──> ExportManager.export_all(document)                 [-> data/documents/<type>/*.md, *.txt]
```

Every generator takes an `LLMProvider` in its constructor (dependency
injection, same pattern as `MatchingEngine`) — **not optional**, unlike
`MatchingEngine`'s rule-based fallback: there is no meaningful non-LLM way
to draft prose, so a missing provider is a configuration error here, not a
graceful-degradation case.

### Reusing three existing subsystems, not rebuilding them

- **`profile.candidate_profile.CandidateProfile`** — built in the previous
  milestone specifically to be "the single source of truth for all future
  AI document generation." This is that future milestone; the profile
  package needed zero changes.
- **`ai.matching_models.JobSnapshot`/`MatchResult`** — reused directly for
  job and match context, rather than inventing a second job-representation
  type. `prompt_builder.py` renders both into prompt text itself (see
  below for why that's not a `CandidateProfile`/`JobSnapshot` method).
- **`ai.llm_provider.LLMProvider`** — the exact same one-method abstraction
  `AnthropicProvider` already implements for the matching engine. No new
  LLM provider abstraction, no new retry logic (`AnthropicProvider` already
  wraps `core.RetryManager`) — this milestone is proof the earlier "the
  architecture should allow additional providers/consumers later without
  changing [it]" claim was true in practice, not just in theory.

### Why prompt rendering lives in `prompt_builder.py`, not on the domain models

`CandidateProfile.to_dict()`/`JobSnapshot` already have ways to serialize
themselves, but "how a profile/job should read when embedded in an LLM
prompt for document generation" is specific to *this* concern. Adding a
`to_prompt_text()` method to `CandidateProfile` would couple a
general-purpose structured-data package to one specific downstream
consumer's formatting needs. `prompt_builder._render_profile()`/
`_render_job()`/`_render_match()` keep that formatting logic where it's
actually used.

### Why this is a new table, not the existing `CoverLetter` model

The original 12-table schema already has a `CoverLetter` model — but it
represents a *finished file* (`file_path`), referenced by
`Application.cover_letter_id`, from the original scraping/application
pipeline design. `GeneratedDocumentRecord` (migration `43d2f89f498d`, new
`generated_documents` table) represents something upstream of that: AI-
drafted *content*, with a review workflow, that a human approves before it
ever becomes a finished file. They're deliberately kept separate — same
reasoning already applied to `profile.CandidateProfile` vs.
`ai.matching_models.CandidateProfile` in earlier milestones. A sensible
future step (see Extension points) is: once a draft is approved and
exported, create a `CoverLetter`/`CV` row pointing at the exported file and
attach it to an `Application`.

`document_type`/`status` are stored as plain `String` columns, not
`sa_enum(...)`, specifically so `database/models/generated_document_record
.py` has zero dependency on the `documents` application package — every
other file in `database/models/` only imports from `database.*`, and this
preserves that invariant rather than creating a downward dependency from
core ORM schema onto a feature package.

## Generating each document type

All three follow the same shape: `Generator.generate(profile, job,
match_result=None, ...) -> GeneratedDocument`. `match_result` is optional —
tailoring is stronger with it (references specific matched strengths) but
generation still works without one (e.g. generating ahead of running the
matching engine).

- **Supporting statement** (`SupportingStatementGenerator`) — 3-5 paragraphs,
  ~400-600 words, no greeting/sign-off (a standalone document).
- **Cover letter** (`CoverLetterGenerator`) — 3-4 paragraphs, ~250-350
  words, formal letter format with greeting and sign-off.
- **Application answer** (`ApplicationAnswerGenerator`) — takes an
  additional `question: str` and `max_words` (default 150). Word limit is
  enforced by **hard truncation after generation**, not just a prompt
  instruction — LLMs don't reliably obey word-count instructions, and a
  real application form's short-answer field often has a hard limit that
  silently exceeding it would make unusable.

### Role-specific tailoring and grounding

Every system prompt shares a common instruction block
(`prompt_builder._GROUNDING_RULES`) that implements two of this milestone's
explicit requirements at the prompt level:

1. **"Avoid inventing experience not in the profile"** — an explicit rule
   telling the model to only reference what's in the supplied profile, and
   to write around gaps honestly rather than fabricate.
2. **"Highlight skills, certificates, NHS/care experience, safeguarding,
   communication, teamwork, confidentiality, dignity, and person-centred
   care"** — listed explicitly as themes to weave in *when truthfully
   grounded in the candidate's actual data*, not unconditionally.

Role-specific tailoring comes from what's embedded in the user prompt:
the full rendered `CandidateProfile`, the specific `JobSnapshot` (title,
employer, band, description, requirements), and — when available — the
`MatchResult`'s `strengths` and `matched_keywords`, so the document
naturally emphasizes what the matching engine already identified as a
strong overlap for this specific job.

## Validation: detecting unsupported claims

A prompt instruction alone cannot guarantee an LLM won't occasionally
fabricate a detail. `DocumentValidator` is the deterministic backstop —
three independent, explainable checks (same design philosophy as
`ai.rule_engine` and `profile.skills_extractor`: simple and explainable now,
not another LLM call):

1. **Uncertified claims** — scans the document for mentions of commonly-
   claimed UK healthcare certificates/training (DBS Check, Manual Handling,
   Basic Life Support, Safeguarding Certificate, Food Hygiene, First Aid)
   and flags (warning) any mentioned that aren't in
   `profile.certificates`.
2. **Unregistered professional bodies** — flags (error — a more serious
   claim) any mention of NMC/HCPC/GMC registration not backed by a matching
   entry in `profile.professional_registrations`.
3. **Inflated years of experience** — extracts any "`N` years" claim from
   the text and cross-checks it against
   `profile.employment_history.total_years_of_experience()` (reused
   directly from the profile subsystem, not reimplemented), flagging
   (warning) claims that exceed the profile's actual total by more than a
   1-year tolerance.

`DocumentService` runs this automatically after every generation: a clean
result sets `DocumentStatus.DRAFT`; any issue sets `DocumentStatus
.NEEDS_REVIEW` instead, so a human reviewer's attention is drawn to exactly
the documents that need closer scrutiny.

## Human-review workflow

```
generate_*()  ──>  DRAFT  (no issues found)
              ──>  NEEDS_REVIEW  (DocumentValidator flagged something)
                       │
          DocumentService.approve()  or  .reject()
                       │
                  APPROVED / REJECTED
```

Nothing transitions a document to `APPROVED` except an explicit
`DocumentService.approve()` call — a human decision, not an automatic
outcome of generation or validation passing. `list_for_review(user_id)`
returns every document still in `DRAFT`/`NEEDS_REVIEW`, i.e. everything
still awaiting that decision. This is the entire "human-review workflow" —
there is no code path anywhere in this package that submits, emails, or
otherwise acts on a document; `approve()` only changes a status column and
optionally records a reviewer's free-text note.

## Export

`ExportManager` writes a `GeneratedDocument` to `data/documents/<document
_type>/` as Markdown (`# <Type>` heading, role/employer/question/status
metadata, the content, and a "Review notes" section listing any validation
issues) and/or plain text (same metadata, no Markdown formatting).
Collision-safe naming (`name (1).md`, `name (2).md`, ...) mirrors
`core.download_manager.DownloadManager`'s pattern. Exporting an
already-persisted document (via `DocumentService.export()`) recovers
`job_title`/`employer` from the `GeneratedDocumentRecord.job` relationship
when set, so the exported filename and header are still meaningful even
though those fields aren't stored as columns on the record itself (only
`job_id` is).

## Extension points

- **Connect approved drafts to `CoverLetter`/`CV`/`Application`.** Once a
  document is `APPROVED` and exported, a future step could create a
  `CoverLetter`/`CV` row pointing at the exported file and attach it via
  `Application.cover_letter_id`/`cv_id` — bridging this subsystem's draft
  workflow into the original application-tracking schema.
- **Semantic (LLM-based) claim validation.** `DocumentValidator`'s checks
  are deterministic keyword/number matching; a future layer could ask an
  LLM "does this paragraph make any claim not supported by this profile?"
  for softer, less enumerable fabrications the current checks can't catch
  syntactically.
- **Caching**, mirroring `ai.cache.MatchCache` — not built this milestone
  since document drafts (unlike match scores) are typically wanted fresh
  per generation request, but the same disk-backed pattern would apply if
  repeated identical generations become common.
- **Additional document types** — add a `DocumentType` value, a
  `build_<type>_system_prompt()`/`build_<type>_user_prompt()` pair, and a
  generator class; `DocumentService` gains a `generate_<type>()` method
  following the existing three's exact shape.

## Known limitations

- **No real LLM calls were made in this milestone.** No Anthropic API key
  is configured in this environment (consistent with every prior AI-related
  milestone) — every test uses `FakeLLMProvider`, a minimal in-test double.
  `AnthropicProvider` itself needed no changes; it's reused as-is.
- **`DocumentValidator`'s checks are syntactic, not semantic.** A
  fabricated claim that doesn't match one of the three specific patterns
  (a known certificate name, a known registration body, a "`N` years"
  phrase) will not be caught. This is a deliberate, documented scope
  boundary, not an oversight — see Extension points for the semantic
  follow-up.
- **Word-limit enforcement is a blunt truncation**, not a request for the
  model to regenerate a shorter answer — acceptable for a draft awaiting
  human review, but a truncated sentence may read awkwardly and should be
  tidied by the reviewer.
- **No connection yet to the existing `CoverLetter`/`CV`/`Application`
  tables** — see Extension points.

## Testing

`tests/test_document_generation.py` — 17 tests, all against a
`FakeLLMProvider` test double (no real LLM calls):

- **Generation**: each of the three generators builds a correctly-grounded
  prompt (verified: `CRITICAL RULES`/theme keywords present in the system
  prompt, candidate/job/match details present in the user prompt) and
  returns a `GeneratedDocument` with the right `DocumentType`; the
  application-answer generator's word-limit truncation is verified both
  when it's needed and when it isn't.
- **Validation**: each of the three unsupported-claim checks (uncertified
  claim, unregistered professional body, inflated years of experience) is
  independently verified to fire, and a genuinely well-grounded document
  produces zero issues.
- **Export**: Markdown and TXT files are actually written to disk with the
  expected content and metadata, including a validation-issues section when
  present; filename collisions are avoided.
- **Persistence/service**: `DocumentRepository` creates/retrieves/updates
  records; `DocumentService` correctly sets `DRAFT` vs. `NEEDS_REVIEW` based
  on validation results; the full approve/reject workflow updates status and
  removes documents from `list_for_review()`; exporting a document reloaded
  from the database (via its `job` relationship) still produces a
  meaningful filename; requesting an action on a nonexistent document id
  raises a clear error.
