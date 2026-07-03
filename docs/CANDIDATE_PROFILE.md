# Candidate Profile Intelligence

`src/job_automation/profile/` builds a complete, structured representation
of a candidate — personal information, education, employment history,
skills, certificates, professional registration, languages, visa status,
and job-search preferences — intended as the single source of truth for
future AI document generation (CVs, cover letters, supporting statements).
**None of that document generation is built yet** — this milestone only
builds the structured representation and its parsing/validation/persistence
infrastructure.

## Relationship to `job_automation.ai.matching_models.CandidateProfile`

There are now two classes named `CandidateProfile` in this codebase, and
that's a deliberate, documented decision rather than an oversight:

| | `ai.matching_models.CandidateProfile` | `profile.candidate_profile.CandidateProfile` |
|---|---|---|
| Built | AI matching engine milestone | This milestone |
| Purpose | Scoring jobs against a candidate | Source of truth for future document generation |
| Shape | Flat, matching-relevant fields only | Rich, nested (personal info, full employment/education entries, certificates as objects, professional registration, languages...) |
| Source | `data/candidate_profile.json` (flat schema) | `data/candidate_profile_full.example.json` (rich schema), or any JSON/YAML/Markdown file |
| Persistence | None (rebuilt from file each time) | `candidate_profiles` table via `ProfileRepository` |

They were **not merged** in this milestone — the task scope was explicitly
"Candidate Profile Intelligence," not a refactor of the already-shipped,
tested AI matching engine. A sensible future step (see Extension points) is
having `ai.profile_builder` delegate to `profile.ProfileService` once this
subsystem is proven, unifying to one schema — but that's future work, not
done here.

## Architecture

```
src/job_automation/profile/
├── candidate_profile.py    — CandidateProfile (aggregate root), PersonalInformation,
│                              ProfessionalRegistration, LanguageProficiency, VisaStatus
├── employment_history.py   — EmploymentEntry, EmploymentHistoryParser, gap/experience analysis
├── education_parser.py     — EducationEntry, EducationParser
├── certificate_parser.py   — Certificate, CertificateParser
├── skills_extractor.py     — SKILL_TAXONOMY, SkillsExtractor (normalization)
├── preference_manager.py   — CandidatePreferences, PreferenceManager (preference logic)
├── cv_parser.py             — CVParser (parses a real, prose-style CV document)
├── profile_loader.py        — ProfileLoader ABC + JSON/YAML/Markdown (+ future PDF/DOCX)
├── profile_validator.py     — ValidationIssue, ProfileValidator
├── profile_service.py       — ProfileService (orchestrates load -> validate -> persist)
└── profile_repository.py    — ProfileRepository (DB persistence, repository pattern)
```

Each "sub-domain" file owns its own entity *and* its own parsing logic
(`EducationEntry` + `EducationParser` together in `education_parser.py`,
etc.) rather than splitting entities and parsers into separate files —
they change together, so they live together. `candidate_profile.py` is the
aggregate root: it imports and composes every sub-entity plus the sections
that don't have a dedicated parser of their own (personal information,
professional registration, languages, visa status).

```
profile_service.py
    ├──> profile_loader.py ──> (JSON | YAML | Markdown) ──> dict ──> CandidateProfile.from_dict()
    ├──> profile_validator.py ──> employment_history.detect_gaps()
    └──> profile_repository.py ──> CandidateProfileRecord (ORM)

cv_parser.py (independent entry point, not used by profile_service.py)
    ├──> employment_history.EmploymentHistoryParser
    ├──> education_parser.EducationParser
    ├──> certificate_parser.CertificateParser
    └──> skills_extractor.SkillsExtractor
```

`cv_parser.py` and `profile_loader.py` solve *different* problems and don't
call each other: `ProfileLoader`s read a file already in this system's own
structured schema (JSON/YAML/Markdown with known section headings);
`CVParser` reads a real CV that was never written with this schema in
mind, and has to split it into sections and interpret each heuristically.
Both delegate their per-section parsing to the same underlying parsers
(`EmploymentHistoryParser`, `EducationParser`, `CertificateParser`) — one
implementation of each, reused by both entry points.

### Why persistence is one JSON column, not many tables

`CandidateProfileRecord` (new model, migration `6b90ec850793`) stores the
entire profile as one JSON blob (`data` column) rather than a table per
section (employment_history, education, certificates, ...). This mirrors
the same reasoning already applied to `Job.requirements`/`JobMatch.analysis`
in earlier milestones: it's a single-user, document-like structure whose
shape may keep evolving, and a dozen additional tables (with all their
foreign keys and migrations) would be disproportionate for what is, in this
project, one profile per user. `CandidateProfileRecord.user_id` is unique —
one profile per user — and cascades from `User` like the project's other
owned-personal-data tables (cvs, applications, certificates, ...).

## Profile schema

```
CandidateProfile
├── personal_information: PersonalInformation
│     full_name, email, phone, address, personal_statement
├── education: [EducationEntry]
│     qualification, awarding_body, year, grade
├── employment_history: [EmploymentEntry]
│     job_title, employer, location, start_date, end_date, responsibilities
│     └── healthcare_experience  — a *derived* filtered view (is_healthcare_role()
│         matches "nhs"/"trust"/"care home"/etc. in employer/title/responsibilities),
│         not a separately-maintained list that could drift out of sync
├── skills: [str]                — normalized against SKILL_TAXONOMY where possible
├── certificates: [Certificate]
│     name, issuing_body, issued_date, expiry_date
├── professional_registrations: [ProfessionalRegistration]
│     body (e.g. "NMC", "HCPC", "GMC"), registration_number, expiry_date, status
├── languages: [LanguageProficiency]
│     language, proficiency
├── visa_status: VisaStatus
│     right_to_work_uk, visa_type, sponsorship_required
├── preferences: CandidatePreferences
│     preferred_locations, max_travel_distance_miles, preferred_employers,
│     preferred_contract_type, preferred_hours, preferred_salary_min,
│     preferred_nhs_band, preferred_working_pattern, remote_preference,
│     visa_sponsorship_required
├── career_goals: [str]
├── availability: str | None      — free text (e.g. "Immediate", "4 weeks notice")
└── keywords: [str]
```

Every entity has `to_dict()`/`from_dict()`, so the whole tree round-trips
through JSON cleanly (verified directly:
`test_profile_repository_saves_and_loads` loads a fixture, saves it,
reloads it, and asserts full equality). See
`tests/fixtures/profile/candidate_profile.json` for a complete example, and
`data/candidate_profile_full.example.json` for the template to copy for
real use.

### Skills taxonomy

15 canonical tags, each with a list of synonym phrases it normalizes from:
`communication`, `patient_care`, `safeguarding`, `moving_and_handling`,
`medication`, `observation`, `documentation`,
`electronic_patient_records`, `leadership`, `nhs_experience`,
`care_experience`, `mental_health`, `learning_disability`, `dementia`,
`community_care`. `SkillsExtractor.normalize()` maps a free-text skill onto
its tag where one matches (keeping unrecognized skills as lowercased,
stripped text rather than dropping them); `SkillsExtractor
.extract_from_text()` scans arbitrary prose (a CV's job responsibilities, a
personal statement) for taxonomy terms — this is how `CVParser` finds
skills a candidate never explicitly listed but clearly has, based on what
their actual job duties describe.

## Validation rules

`ProfileValidator.validate()` returns a list of `ValidationIssue(field,
severity, message)` — `severity` is `"warning"` (informational, the profile
is still usable) or `"error"` (a genuine contradiction). Never blocks
anything; a caller decides what to do with the issues.

- **Missing skills** — warning if `skills` is empty.
- **Incomplete employment history** — warning if `employment_history` is
  empty, or if any entry has no `employer` or no `start_date`; warning per
  gap of more than ~6 months detected between consecutive roles
  (`employment_history.detect_gaps()`).
- **Missing qualifications** — warning if `education` is empty.
- **Missing certificates** — warning if `certificates` is empty; if not
  empty but missing DBS/Manual Handling/Basic Life Support specifically
  (commonly expected for UK healthcare roles), a separate warning per
  missing one — checked only when the list is non-empty, since an entirely
  empty list already gets the more general warning above.
- **Conflicting information** —
  - error if `visa_status.right_to_work_uk` is `True` *and*
    `visa_status.sponsorship_required` is also `True` (contradictory: if you
    have the right to work, you don't need sponsorship).
  - error if a `professional_registrations` entry has `status == "active"`
    but its `expiry_date` is in the past.

## Preference engine

`PreferenceManager` provides the actual comparison logic behind each
preference field — `matches_location()`, `matches_employer()`,
`meets_salary_expectation()`, `matches_working_pattern()`,
`satisfies_visa_requirement()` — each returning `True` when the candidate
expressed no preference at all (compatible with anything) rather than
`False`, so an unset preference is never mistaken for exclusion. Kept
independent of `job_automation.ai.rule_engine` (which does similar
comparisons for job *matching*) — see the coexistence note above; this
gives preference logic a home within the candidate-profile subsystem
without a cross-package dependency on `ai`.

## Extension points

- **Migrate `ai.profile_builder` to this subsystem.** Once proven, have it
  build a `profile.CandidateProfile` via `ProfileService` and adapt to
  `ai.matching_models.CandidateProfile` at the boundary, retiring the
  simpler flat JSON schema in favor of one source of truth.
- **CV/cover letter/supporting statement generation** (explicitly out of
  scope this milestone) — would consume `CandidateProfile` as its input.
- **New skill taxonomy tags** — add to `SKILL_TAXONOMY` in
  `skills_extractor.py`; no other file needs to change.
- **New preference fields** — add to `CandidatePreferences` in
  `preference_manager.py`, plus a corresponding comparison method if it
  needs matching logic, not just storage.
- **New validation rules** — add a `_check_*` method to `ProfileValidator`
  and call it from `validate()`.
- **A future "confidence" or "completeness" score** for a profile,
  analogous to `ai.MatchResult.confidence_score` — `ProfileValidator`
  already has everything needed (the issue list) to derive one.

## Future PDF parsing

`PDFProfileLoader` and `DOCXProfileLoader` (in `profile_loader.py`) are real
classes implementing the `ProfileLoader` interface today — calling `.load()`
raises `NotImplementedError` with a clear message, since no PDF/DOCX-reading
library (e.g. `pdfplumber`, `python-docx`) is a project dependency yet, and
adding one isn't justified until a milestone actually implements this. This
is a deliberate "future interface," not an oversight — the point of having
`ProfileLoader` as a shared abstraction is that implementing these later
requires:

1. Add the chosen library to `requirements.txt`.
2. Implement `load()` in the existing class — extract text (PDF: page text
   via the library's extraction API; DOCX: paragraph text via
   `python-docx`), then very likely delegate to `CVParser` for the actual
   section-splitting and structured extraction, exactly as a `.txt`/`.md`
   CV would be handled today.
3. Nothing in `ProfileService`, `get_loader_for_path()`, or any caller
   needs to change — `_LOADERS_BY_SUFFIX` already routes `.pdf`/`.docx` to
   these classes.

## Known limitations

- **Text parsing (`CVParser`, `EducationParser`, `CertificateParser`,
  `EmploymentHistoryParser`) is heuristic, not NLP-based.** It handles
  reasonably well-structured documents (headings on their own line, one
  entry per line/block) correctly — verified against a realistic sample CV
  — but will not extract anything sensible from a heavily-designed,
  columnar, or image-based CV layout. That's a fundamentally different
  problem (visual layout parsing) from text parsing.
- **Markdown's "issued/expires" and "employment date range" parsing use ISO
  dates or explicit keywords.** A certificate line with a bare year and no
  "issued"/"expires" keyword (e.g. "Manual Handling, 2023-03-01") falls back
  to capturing only the year, not the full date — documented and tested
  behavior (`test_cv_parser_extracts_structured_data_from_a_real_cv`
  implicitly covers this), not a bug.
- **Employer-quality / registration-number-format validation are not
  checked.** `ProfileValidator` checks for missing/conflicting data, not
  whether a registration number matches NMC/HCPC/GMC's actual format rules.
- **`total_years_of_experience()` and `detect_gaps()` skip entries with
  unparseable dates** rather than erroring — a badly-formatted date reduces
  the precision of these calculations rather than breaking them entirely.

## Testing

`tests/test_candidate_profile.py` — 25 tests, all against local fixtures
(`tests/fixtures/profile/`):

- **Loaders**: JSON parses a complete profile; YAML produces an object
  equal to the JSON version (same schema, different serialization);
  Markdown parses every section correctly (including the "Present" vs
  `null` end-date distinction between text and structured formats);
  unknown extensions raise `ValueError`; PDF/DOCX loaders raise
  `NotImplementedError` as documented future interfaces.
- **CV parsing**: a realistic sample CV's employment history, education,
  certificates (with issued/expiry dates), and skills (both explicit and
  inferred from prose) all extract correctly.
- **Normalization**: known taxonomy terms normalize correctly, unrecognized
  skills are kept rather than dropped, duplicates are removed, and prose
  scanning finds taxonomy terms not in an explicit skills list.
- **Employment analysis**: healthcare-role detection, filtering, total
  years of experience, and gap detection (including *not* flagging a short
  gap) are all verified independently of a full profile.
- **Validation**: an empty profile is flagged on every missing section; a
  non-empty-but-incomplete certificate list is flagged for missing DBS
  specifically; visa contradictions and expired-but-active registrations
  are flagged as errors; a complete, well-formed profile produces zero
  errors.
- **Preferences**: location/salary/visa matching logic, including the "no
  preference expressed = compatible with anything" behavior.
- **Persistence**: `ProfileRepository` round-trips a full profile through
  the database with exact equality; saving twice for the same user updates
  the existing row rather than duplicating it; `ProfileService`'s full
  load-save-validate-get flow works end-to-end.
