"""Vendored built-in agent templates.

Loaded by :mod:`app.services.agent_templates.loader` into the system
workspace as public marketplace agents. The directory layout is

    templates/
        <category>/<slug>.md   ← persona Markdown with YAML front-matter
        catalog.py             ← category labels + curated tag overrides
        LICENSE                ← upstream MIT (compliance only, not code)

See :mod:`app.agents.templates.catalog` for the canonical category list
and tag-override map.
"""
