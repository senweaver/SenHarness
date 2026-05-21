"""Built-in agent templates — vendored Markdown personas with curated tags.

Loaded by :mod:`app.services.seed` at ``make seed`` time and by the
``seed-templates`` CLI command. Surfaces in the marketplace via
``GET /agents/discover`` (rows where ``metadata_json.template = true``).
"""

from app.services.agent_templates import loader

__all__ = ["loader"]
