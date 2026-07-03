"""
`JobProvider` is the one abstraction every job board plugs into: "given a
database session, download this source's current listings, normalize them
into `ParsedJob`s, deduplicate against what's already stored, and persist
the result." That whole pipeline is one method, `fetch_jobs()`, rather than
four separate abstract steps — normalization/dedup/persistence are already
fully generic (`database.services.JobIngestionService`, reused unchanged
by every real provider below), so forcing each provider to re-expose them
as separate abstract methods would just be ceremony around calling the
same shared service. What genuinely differs per provider is *how the raw
listings are fetched* (a Playwright scraper for an HTML-only site vs. a
plain HTTP client for a JSON API), which is exactly what `fetch_jobs()`
leaves up to the subclass.

`NHSProvider`/`TracProvider` wrap the existing (or new) Playwright-based
`scrapers.base.BaseScraper` subclasses, which already fetch+normalize+
persist internally in one `.run()` call (see `NHSScraper.scrape()` for why:
detail-page enrichment happens interleaved with search-result paging, not
as a separate later pass). `ReedProvider` calls Reed's JSON API directly
via `httpx` (no browser needed) and persists via the same
`JobIngestionService`. `IndeedProvider`/`TotalJobsProvider` raise
`NotImplementedError` — see their own module docstrings for why.
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import ClassVar

from sqlalchemy.orm import Session


@dataclass
class ProviderRunStats:
    source: str
    jobs_seen: int = 0
    jobs_created: int = 0
    jobs_updated: int = 0
    jobs_failed: int = 0
    #: IDs of every newly-inserted Job from this run — the input set for
    #: `ingestion.auto_match_service`'s "match + notify" step, which should
    #: only ever run against genuinely new listings, not re-imports of
    #: jobs a user may have already seen and dismissed.
    newly_created_job_ids: list[uuid.UUID] = field(default_factory=list)


class JobProvider(ABC):
    #: Matches `Job.source_site` — the identity every persisted job from
    #: this provider is stored and deduplicated under.
    source_name: ClassVar[str]

    @abstractmethod
    def fetch_jobs(self, session: Session) -> ProviderRunStats:
        """Download, normalize, deduplicate, and persist this provider's
        current listings. Providers with no compliant data source today
        (Indeed, TotalJobs) raise `NotImplementedError` with a clear
        explanation instead of silently returning an empty result — a
        provider that "ran successfully but found nothing" and one that
        "cannot run at all yet" are different facts callers should be able
        to tell apart."""
