"""Platform-builtin agents.

These agents are wired into the runtime directly — they do not have
a row in the ``agents`` table and are never visible in the workspace
agent listing. Each module exposes a single ``invoke_*_subagent``
entry point so callers (ARQ jobs, admin endpoints) treat the agent
as a one-shot side-effect rather than an interactive chat target.
"""
