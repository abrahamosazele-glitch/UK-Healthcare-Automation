# Application Workflow Management

`src/job_automation/workflows/` manages the internal journey from a matched
job to a reviewed, ready-to-apply status and beyond — connecting `Job`,
`JobMatch`, the candidate's `CandidateProfile`, and `GeneratedDocumentRecord`
(built in the previous three milestones) into one trackable aggregate per
(user, job) pair.

**No automatic application submission.** No method in this package submits,
sends, or applies to anything. `mark_applied()` only *records* a fact that
already happened outside this system — see "Preventing automatic
submission" below.

## Architecture

```
src/job_automation/workflows/
├── workflow_models.py       — WorkflowStatus, StatusHistoryEntry, AuditLogEntry, ChecklistItem, ApplicationChecklist
├── application_workflow.py  — ApplicationWorkflow (pure state-machine rules), InvalidTransitionError
├── status_manager.py        — StatusManager (validates + performs a transition, records history)
├── review_service.py        — ReviewService (approve/reject, built on StatusManager)
├── checklist_service.py     — ChecklistService (computes the ready-to-apply checklist)
├── audit_log.py             — AuditLog (thin wrapper recording audit entries)
├── workflow_repository.py   — WorkflowRepository (repository pattern)
└── workflow_service.py      — WorkflowService (orchestrates all of the above)
```

```
WorkflowService
    ├──> WorkflowRepository.create/get/find_by_job_and_user/list_for_user(...)
    ├──> StatusManager.transition(workflow, target)
    │        └──> ApplicationWorkflow.validate_transition(current, target)  [raises InvalidTransitionError if not allowed]
    │        └──> WorkflowRepository.add_status_history(...)  [-> WorkflowStatusHistoryRecord]
    ├──> ReviewService.approve/reject(workflow)
    │        └──> StatusManager.transition(...) + AuditLog.record(...)
    ├──> ChecklistService.build_checklist(profile, documents)
    └──> AuditLog.record(workflow, action, details)  [-> WorkflowAuditLogRecord]
```

### Why `application_workflow.py`, `status_manager.py`, and `review_service.py` are three separate files

Each owns a distinct layer of the same concern, from most to least generic:

1. **`application_workflow.py`** — the *rules*: which `WorkflowStatus` can
   move to which. Pure, dependency-free, no side effects.
2. **`status_manager.py`** — the *mechanism*: actually performs a validated
   transition and records it in status history. Knows nothing about *why* a
   transition is happening (approval, rejection, marking applied — all look
   identical to this class).
3. **`review_service.py`** — the *business meaning* of one specific kind of
   transition (a human review decision): records *why* in the audit log, in
   addition to the bare status change `StatusManager` already logs.

This mirrors the same layering already used elsewhere in this project (e.g.
`core.retry_manager.RetryManager` is the generic mechanism; `AnthropicProvider`
gives it business meaning for one specific kind of call).

### Reusing, not duplicating, three existing subsystems

- **`profile.CandidateProfile`** — passed into `ChecklistService
  .build_checklist()` and used as-is; no changes needed.
- **`documents.document_models.DocumentType`/`DocumentStatus`** — reused
  directly by `ChecklistService` (a legitimate application-layer reuse, not
  a database-layer one, so it doesn't touch the "database models only
  import `database.*`" invariant used elsewhere).
- **`profile.employment_history`/`ai.matching_models`** — not touched at
  all this milestone; `JobSnapshot`/`MatchResult` aren't referenced here
  because a workflow only needs to know *which* job/match it's for
  (`job_id`/`job_match_id`), not their full content — that's already
  persisted on the `Job`/`JobMatch` rows themselves.

## Workflow statuses

```
NEW_MATCH ──► DOCUMENTS_GENERATED ──► NEEDS_REVIEW ──┬─► APPROVED ──► READY_TO_APPLY ──► APPLIED ──► INTERVIEW ──► OFFER ──► CLOSED
                     ▲                                └─► REJECTED ──┘
                     └───────────────────────────────────────┘
   (CLOSED is also reachable directly from every one of the above — a candidate can abandon a job at any stage)
```

| Status | Meaning |
|---|---|
| `NEW_MATCH` | A `JobMatch` exists; nothing else has happened yet. |
| `DOCUMENTS_GENERATED` | At least one document has been generated for this job. |
| `NEEDS_REVIEW` | Submitted for human review. |
| `APPROVED` | A human approved the generated documents. |
| `REJECTED` | A human rejected them — loops back to `DOCUMENTS_GENERATED` on the next `attach_document()` call, so regenerating and resubmitting is a normal part of the cycle, not a dead end. |
| `READY_TO_APPLY` | Approved and ready for the candidate to apply manually. |
| `APPLIED` | The candidate has manually applied (see below). |
| `INTERVIEW` | Invited to interview. |
| `OFFER` | An offer has been made. |
| `CLOSED` | Terminal — no longer being pursued, for any reason (withdrawn, declined, rejected by employer, etc.). |

`CLOSED` has no outgoing transitions (`ApplicationWorkflow.is_terminal()`
returns `True` for it) — verified directly in
`test_application_workflow_closed_is_terminal`.

## Human review steps and the approve/reject workflow

A workflow only reaches `APPROVED` or `REJECTED` via `WorkflowService
.approve()`/`.reject()` — both go through `ReviewService`, which:

1. Calls `StatusManager.transition()` (validates the move is legal from the
   current status, records it in `workflow_status_history`).
2. Records an `"approved"`/`"rejected"` entry in the audit log, including
   the reviewer's free-text notes.

There is no code path that sets a workflow to `APPROVED`/`REJECTED` except
these two explicit calls — generation (`attach_document()`) only ever
reaches `DOCUMENTS_GENERATED`, never further, until `submit_for_review()`
and then a human decision are both made.

## Application checklist and tracking missing documents

`ChecklistService.build_checklist(profile, documents)` returns an
`ApplicationChecklist` with five items:

1. **Supporting statement** — exists and is `APPROVED` (not still
   draft/needs-review/rejected).
2. **Cover letter** — same check.
3. **Certificates on file** — `profile.certificates` is non-empty.
4. **Visa status confirmed** — `profile.visa_status.right_to_work_uk` is set
   (not `None`).
5. **All documents approved** — no linked document, of any type, is still
   awaiting approval.

`checklist.is_complete` is `True` only when every item passes;
`checklist.missing_items` lists exactly what's outstanding — this is
"track missing documents" directly. `WorkflowService.build_checklist()`
fetches every document ever linked to the workflow
(`WorkflowRepository.get_documents()`, ordered oldest-first) and the
checklist logic always considers only the **latest** row per document type
(`ChecklistService._latest_per_type()`), so an older, superseded, or
rejected draft never blocks the checklist once a newer one has been
generated and approved.

## Tracking document versions

`GeneratedDocumentRecord` (from the Document Generation milestone) never
updates in place — every call to `DocumentService.generate_*()` inserts a
new row. This milestone adds `GeneratedDocumentRecord.workflow_id`
(nullable, `ON DELETE SET NULL`) linking a document to the workflow it was
generated for. "Track document versions" therefore falls out of the
existing schema for free: `WorkflowRepository.get_documents(workflow_id)`
returns every version ever generated for that workflow, oldest first —
verified directly in `test_rejection_loops_back_to_documents_generated`,
which regenerates a document after a rejection and confirms **both**
versions remain linked and queryable, nothing is lost.

## Tracking status history and storing audit logs

Both get their own tables — `workflow_status_history` and
`workflow_audit_logs` — rather than JSON blobs on the main
`application_workflows` row (unlike `JobMatch.analysis`/
`CandidateProfileRecord.data`/`GeneratedDocumentRecord.validation_issues`,
which *are* JSON blobs). An append-only historical/audit trail is exactly
the kind of data that should be immutable and independently queryable, not
editable via a normal `UPDATE` on its parent row the way an evolving JSON
blob is — a deliberately different storage choice from the JSON-blob
pattern used elsewhere, made for a genuinely different kind of data.

- **`WorkflowStatusHistoryRecord`** — one row per transition
  (`from_status`, `to_status`, optional `note`, `created_at`). The very
  first entry has `from_status = None` (workflow creation itself is
  recorded as a "transition" into `NEW_MATCH`).
- **`WorkflowAuditLogRecord`** — one row per significant action (`action`
  string + a `details` JSON dict), covering more than just status changes:
  `workflow_started`, `document_attached`, `submitted_for_review`,
  `approved`/`rejected` (with reviewer notes), `marked_ready_to_apply`,
  `marked_applied`, `marked_interview`, `marked_offer`, `closed`.

They're independent of each other by design — verified directly in
`test_audit_log_persists_independently_of_status_history` (starting a
workflow produces exactly one row in each, for different reasons: one
records the `NEW_MATCH` transition, the other records the
`workflow_started` action).

## Preventing automatic submission

There is exactly one way a workflow reaches `APPLIED`:
`WorkflowService.mark_applied(workflow, note=...)`, called explicitly. No
scraper, no browser-automation code, no scheduled job anywhere in this
codebase calls this method — searching the codebase for callers of
`mark_applied` finds only this milestone's own tests and (in the future) a
human-facing UI/CLI action. This is what "prevent automatic submission"
means concretely: the *capability* to record an application exists (the
whole point of the workflow), but the *action* of applying is never
triggered by code — only by a person telling the system what they already
did. The explicit prohibition is also documented directly in
`workflow_service.py`'s module docstring and `mark_applied()`'s own
docstring, so it's visible to anyone extending this code later, not just in
this doc.

## Bug found and fixed while verifying this milestone

While re-running `scripts/verify_browser_framework.py` as part of this
milestone's regression check, a real network failure (DNS couldn't resolve
`example.com` on the local network — unrelated to this project) surfaced a
latent bug in `job_automation.core.retry_manager.RetryManager`, present
since the Browser Framework milestone but never triggered before now
(no previous test or run had ever hit a *genuine* exception through its
default `retry_on` path): `except retry_on as exc:` failed with `TypeError:
catching classes that do not inherit from BaseException is not allowed`,
because `TransientError` (the default retry marker) is a plain mixin, not
itself an exception type — valid for `isinstance()` checks, but not for a
bare `except (...)` clause. Fixed by matching with
`except Exception as exc: if not isinstance(exc, retry_on): raise` instead,
which works correctly for a mixin-based marker without requiring it to also
become an exception subclass. Verified directly: a transient exception now
retries the correct number of times and raises `RetryExhaustedError`; a
non-transient exception now propagates immediately, unretried, exactly as
designed. This fix benefits every consumer of `RetryManager`
(`BrowserManager`, `PageManager`, `AnthropicProvider`), not just this
milestone.

## Extension points

- **A human-facing review surface.** `WorkflowService.list_for_user()`
  (workflows still needing attention) and `approve()`/`reject()` are ready
  to be wired into a future dashboard/CLI — explicitly out of scope this
  milestone ("do not begin dashboard yet").
- **Automatic status inference from external signals** (e.g. an email
  parser detecting an interview invite) could call `mark_interview()` —
  but that's a future, explicitly-scoped integration, not something this
  milestone builds or implies is safe to add without equal care around
  "prevent automatic submission" for `mark_applied()` specifically.
- **Connecting to `CoverLetter`/`CV`/`Application`** — once a workflow
  reaches `READY_TO_APPLY`, a future step could create the original
  schema's `Application` row (with its `cv_id`/`cover_letter_id`) pointing
  at the approved `GeneratedDocumentRecord`s — the same future connection
  point already noted in docs/DOCUMENT_GENERATION.md.
- **Configurable checklist items** — `ChecklistService`'s five checks are
  currently fixed; a future version could accept a list of required
  document types/profile fields per job type (e.g. some roles don't need a
  cover letter).

## Testing

`tests/test_application_workflow.py` — 16 tests, no real LLM calls (documents
are generated via `FakeLLMProvider`, same pattern as the prior two
milestones' test suites), no browser automation, no scraping:

- **State machine rules** (pure, no database): the full documented forward
  journey is allowed step by step; rejection can loop back to
  `DOCUMENTS_GENERATED`; `CLOSED` is reachable from every non-terminal
  status and is itself terminal; three specific invalid transitions
  (`NEW_MATCH`→`APPROVED`, `CLOSED`→`NEW_MATCH`, `APPLIED`→`APPROVED`) are
  all rejected with `InvalidTransitionError`.
- **Checklist**: a bare-minimum profile with no documents flags all five
  items as missing; a checklist correctly prefers a newer, approved
  document version over an older, rejected one for the same document type.
- **Full service journey**: `start_workflow()` is idempotent per (user,
  job); attaching a document advances `NEW_MATCH`→`DOCUMENTS_GENERATED`;
  the complete journey from match to `CLOSED` is walked end-to-end with the
  exact expected status-history sequence asserted; rejecting and
  regenerating correctly loops back and preserves both document versions;
  an invalid transition attempt (`mark_applied()` from `NEW_MATCH`) is
  rejected.
- **Audit log**: every significant action across a real journey is
  recorded in order, including a reviewer's notes on approval; audit log
  and status history are confirmed to persist independently of each other.
- **Repository/persistence**: `list_for_user()` orders workflows most-
  recent-first; deleting a workflow correctly `SET NULL`s
  `GeneratedDocumentRecord.workflow_id` on its linked documents rather than
  deleting the documents themselves (the draft survives, just
  disassociated from the now-deleted workflow).
