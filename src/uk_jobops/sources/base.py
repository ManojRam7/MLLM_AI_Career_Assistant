"""Common interface for all job-discovery sources."""
from __future__ import annotations

from dataclasses import dataclass, field

from ..models import Job


@dataclass
class SourceResult:
    source: str
    jobs: list[Job] = field(default_factory=list)
    status: str = "ok"          # ok | skipped | error
    message: str = ""
    meta: dict = field(default_factory=dict)   # optional extras (e.g. per-company coverage)


class Source:
    name = "base"

    def fetch(self, *, queries: list[str], locations: list[str], recency_days: int, limit: int) -> SourceResult:
        raise NotImplementedError
