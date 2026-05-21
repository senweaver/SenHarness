"""Echo logger plugin entry point.

The host calls :func:`register` exactly once at startup, after the
plugin manifest has been validated and the platform admin has flipped
``platform_settings.plugins.allow_user_plugins`` to ``True``. The
context object enforces ``capability_scopes`` so attempting to wire a
hook outside the manifest raises and the loader records the failure
as ``plugin.load_failed`` — there is no way for this plugin to attach
itself to ``pre_llm_call`` without first declaring the scope.
"""

from __future__ import annotations


def register(ctx) -> None:
    async def _on_pre_tool(*, tool_name: str, **_kwargs: object) -> None:
        print(f"[echo_logger] pre_tool_call: {tool_name}")  # noqa: T201

    async def _on_post_tool(
        *, tool_name: str, ok: bool = True, **_kwargs: object
    ) -> None:
        status = "ok" if ok else "error"
        print(  # noqa: T201
            f"[echo_logger] post_tool_call: {tool_name} -> {status}"
        )

    ctx.register_hook("pre_tool_call", _on_pre_tool)
    ctx.register_hook("post_tool_call", _on_post_tool)
