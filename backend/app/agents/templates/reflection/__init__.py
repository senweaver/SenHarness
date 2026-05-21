"""Reflection prompt templates for the L3 runner.

These markdown files are loaded by :mod:`app.agents.templates.reflection.loader`
and injected into the agent loop as ephemeral system messages by the M0.4
periodic reflection hook and the M0.5 GAPA tool-call reflection hook. They
never reach persisted ``message_history`` and never reach the user.
"""

from .loader import (
    REFLECTION_TEMPLATE_NAMES,
    TemplateNotFoundError,
    list_templates,
    load_reflection_template,
)

__all__ = [
    "REFLECTION_TEMPLATE_NAMES",
    "TemplateNotFoundError",
    "list_templates",
    "load_reflection_template",
]
