"""Whitelisted reflection template loader.

Used by the M0.4 / M0.5 reflection hook in :mod:`app.agents.harness.reliability`.
Templates are vendored markdown files under this directory and resolved by
*name only* — callers cannot supply a relative path. This guards against path
traversal (``../../etc/passwd``) and accidental loads from arbitrary user
content. Bodies are cached in memory after first read; the on-disk files are
read-only at runtime.
"""

from __future__ import annotations

from pathlib import Path
from threading import Lock

# Names map 1:1 to ``<name>.md`` files in this directory. Adding a new
# template requires a code change here — that's the whitelist.
REFLECTION_TEMPLATE_NAMES: frozenset[str] = frozenset({"periodic", "tool_call"})

_TEMPLATE_DIR = Path(__file__).parent
_CACHE: dict[str, str] = {}
_CACHE_LOCK = Lock()


class TemplateNotFoundError(LookupError):
    """Raised when ``name`` is not in :data:`REFLECTION_TEMPLATE_NAMES`
    or the corresponding ``<name>.md`` file is missing on disk."""


def load_reflection_template(name: str) -> str:
    """Return the body of the named reflection template.

    The name must be a member of :data:`REFLECTION_TEMPLATE_NAMES`; anything
    else raises :class:`TemplateNotFoundError` (including names containing
    path separators or ``..``). First call reads from disk and memoises;
    later calls are O(1) dict lookups under a process-wide lock.
    """
    if name not in REFLECTION_TEMPLATE_NAMES:
        raise TemplateNotFoundError(name)
    cached = _CACHE.get(name)
    if cached is not None:
        return cached

    path = _TEMPLATE_DIR / f"{name}.md"
    # Defensive: even though the whitelist is a frozenset of plain identifiers,
    # double-check the resolved path didn't escape the template directory.
    resolved = path.resolve()
    if _TEMPLATE_DIR.resolve() not in resolved.parents:
        raise TemplateNotFoundError(name)

    if not resolved.is_file():
        raise TemplateNotFoundError(name)

    body = resolved.read_text(encoding="utf-8").strip()
    with _CACHE_LOCK:
        _CACHE[name] = body
    return body


def list_templates() -> list[str]:
    """Stable, sorted view of the whitelist for diagnostic endpoints."""
    return sorted(REFLECTION_TEMPLATE_NAMES)


def _clear_cache_for_tests() -> None:
    """Test-only seam — production code never invalidates the cache."""
    with _CACHE_LOCK:
        _CACHE.clear()
