"""
The AI Career Assistant: turns an already-computed `MatchResult` (from the
existing AI matching engine — see `job_automation.ai`) into plain-English,
actionable insight for one job — a score explanation, prioritized CV
suggestions, and a predicted interview-readiness level. See
docs/CAREER_ASSISTANT.md for the full design.

Deliberately two tiers, mirroring the rest of this codebase's "rule-based
always-on, real LLM call explicit-and-optional" convention (the same
split used by AI matching itself and every notification's
in-app-vs-email decision):

- `career_assistant_service.CareerAssistantService.build_insight()` — pure,
  deterministic, zero cost, zero LLM call. Runs on every page load a job
  match is shown on; nothing here can ever incur real spend or require
  network access.
- `job_automation.documents.career_insight_generator.CareerInsightGenerator`
  — an optional, explicit-click, real-LLM-backed narrative version,
  wired into the pre-existing document-generation pipeline
  (`DocumentType.CAREER_INSIGHT`) rather than a new bespoke one.
"""
