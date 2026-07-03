"""
Duplicate detection for scraped job listings.

Will implement two layers:
  1. Exact match: (source_site, external_id) already exists in the Job table.
  2. Fuzzy match: same employer + very similar title/location posted within a
     few days but re-listed with a new ID (common on NHS Jobs/Indeed reposts) —
     detected via a normalized content_hash (lowercased title+employer+location)
     and/or a string-similarity threshold, to catch near-duplicates that
     exact-ID matching would miss.
Exposes `filter_new_jobs(raw_jobs: list[RawJobListing]) -> list[RawJobListing]`
used by main.py before anything is written to the database.
"""
