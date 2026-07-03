"""
Pydantic-based validation for data crossing module boundaries.

Candidates: validating a RawJobListing has the minimum required fields before
it's handed to deduplication/database code, and validating the candidate
profile JSON (data/candidate_profile.json) loaded at startup so a malformed
profile fails fast with a clear error instead of breaking AI generation later.
"""
