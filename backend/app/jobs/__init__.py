"""ARQ background jobs.

Each task in this package must:
- accept ``ctx: dict`` first (ARQ contract).
- return a JSON-serialisable summary dict (used by ``arq``'s result
  store and by audit-replay tooling).
- log a permanent failure to ``audit_events(action="job.failed_permanent")``
  after exceeding its retry budget instead of silently dropping the work.
"""

from __future__ import annotations
