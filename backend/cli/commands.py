"""SenHarness CLI entry (django-style subcommands).

Usage::

    python -m cli.commands server run
    python -m cli.commands create-admin
    python -m cli.commands seed
    python -m cli.commands migrate
    python -m cli.commands scheduler run
"""

from __future__ import annotations

import asyncio
import sys
from getpass import getpass

import click
import uvicorn
from rich.console import Console

from app.core.config import settings

console = Console()


@click.group()
def cli() -> None:
    """SenHarness command-line interface."""


# ─── server ───────────────────────────────────────────────
@cli.group()
def server() -> None:
    """Manage the API server."""


@server.command("run")
@click.option("--host", default=None, help="Bind host")
@click.option("--port", default=None, type=int, help="Bind port")
@click.option("--reload/--no-reload", default=None)
def server_run(host: str | None, port: int | None, reload: bool | None) -> None:
    """Run uvicorn.

    In reload mode we restrict watchfiles to the first-party source
    directories (``app/`` and ``cli/``). Two reasons:

    1. Without this uvicorn watches the entire CWD — and on a host
       venv every ``pip install`` (e.g. an optional channel SDK)
       explodes into a noisy reload because
       ``.venv/Lib/site-packages/**`` looks like code changing.
    2. ``reload_excludes`` only filters change events *after* watch
       paths are enumerated; on Windows enumerating a 30k-file
       site-packages tree at startup pegs CPU for minutes and the
       child process never finishes booting.

    We pass *absolute* paths because uvicorn's reload supervisor
    serializes them to subprocess argv and relative paths can
    re-resolve to the wrong CWD on Windows.
    """
    use_reload = reload if reload is not None else settings.APP_DEBUG
    kwargs: dict = {
        "host": host or settings.BACKEND_HOST,
        "port": port or settings.BACKEND_PORT,
        "reload": use_reload,
        "access_log": settings.APP_DEBUG,
    }
    if use_reload:
        from pathlib import Path

        backend_root = Path(__file__).resolve().parent.parent
        kwargs["reload_dirs"] = [
            str(backend_root / "app"),
            str(backend_root / "cli"),
        ]
    uvicorn.run("app.main:app", **kwargs)


# ─── create-admin ─────────────────────────────────────────
@cli.command("create-admin")
@click.option("--email", prompt=True)
@click.option("--name", prompt=True)
def create_admin_cmd(email: str, name: str) -> None:
    """Create a platform-admin identity."""
    password = getpass("Password: ")
    confirm = getpass("Confirm: ")
    if password != confirm:
        console.print("[red]Passwords do not match[/red]")
        sys.exit(1)

    async def _run() -> None:
        # Local import to avoid loading DB during --help
        from app.db.session import get_session_factory
        from app.services.admin import create_platform_admin

        async with get_session_factory()() as session:
            identity = await create_platform_admin(session, email=email, name=name, password=password)
            await session.commit()
            console.print(f"[green]Admin created: {identity.email}[/green]")

    asyncio.run(_run())


# ─── seed ─────────────────────────────────────────────────
@cli.command("seed")
def seed_cmd() -> None:
    """Seed demo workspace + default agent + built-in templates."""

    async def _run() -> None:
        from app.db.session import get_session_factory
        from app.services.seed import seed_defaults

        async with get_session_factory()() as session:
            summary = await seed_defaults(session)
            await session.commit()
            for line in summary:
                console.print(line)

    asyncio.run(_run())


# ─── seed-templates ───────────────────────────────────────
@cli.command("seed-templates")
def seed_templates_cmd() -> None:
    """Refresh the vendored built-in agent templates only.

    Use after editing files under ``backend/app/agents/templates/`` —
    cheaper than a full ``seed`` because it skips the demo workspace
    setup. Idempotent: existing templates are upserted by
    ``metadata_json.template_slug``, never duplicated.
    """

    async def _run() -> None:
        from app.db.session import get_session_factory
        from app.services.agent_templates import loader as tpl_loader
        from app.services.seed import (
            _ensure_system_identity,
            _ensure_system_workspace,
        )

        async with get_session_factory()() as session:
            sys_identity = await _ensure_system_identity(session)
            sys_ws_id, _ = await _ensure_system_workspace(
                session, owner_identity_id=sys_identity.id
            )
            created, updated = await tpl_loader.load_all(
                session,
                system_workspace_id=sys_ws_id,
                system_identity_id=sys_identity.id,
            )
            await session.commit()
            console.print(
                f"[green]Templates refreshed[/green] — {created} created, "
                f"{updated} updated"
            )

    asyncio.run(_run())


# ─── migrate ──────────────────────────────────────────────
@cli.command("migrate")
def migrate_cmd() -> None:
    """Run Alembic `upgrade head`."""
    import subprocess

    subprocess.run(["alembic", "upgrade", "head"], check=True)


# ─── scheduler ────────────────────────────────────────────
@cli.group()
def scheduler() -> None:
    """Manage the APScheduler process."""


@scheduler.command("run")
def scheduler_run() -> None:
    """Run the background scheduler."""

    async def _run() -> None:
        from app.core.logging import setup_logging
        from app.workflows.scheduler import run_forever

        setup_logging()
        await run_forever()

    asyncio.run(_run())


# ─── channels ─────────────────────────────────────────────
@cli.group()
def channels() -> None:
    """Manage the IM channel runtime (stream connections)."""


@channels.command("run")
def channels_run() -> None:
    """Run the IM channel stream supervisor as a standalone worker.

    Recommended for production multi-worker deployments: set
    ``CHANNEL_RUNTIME_INPROCESS=False`` so the API tier stays focused on
    HTTP and let a single dedicated process own the long-lived IM links
    (Feishu / Lark / DingTalk / WeCom / Discord / QQ / WeChat-iLink).
    """

    async def _run() -> None:
        from app.core.logging import setup_logging
        from app.security.keyring import ensure_master_key_on_startup
        from app.services.channel_runtime import get_runtime

        setup_logging()
        ensure_master_key_on_startup()

        runtime = get_runtime()
        await runtime.start_all()
        console.print("[green]ChannelRuntime started — Ctrl+C to stop[/green]")

        try:
            while True:
                await asyncio.sleep(3600)
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            await runtime.stop_all()
            console.print("[yellow]ChannelRuntime stopped[/yellow]")

    asyncio.run(_run())


@channels.command("wechat-login")
@click.argument("channel_id")
def channels_wechat_login(channel_id: str) -> None:
    """Print a one-shot QR-login URL/payload for a WeChat-iLink channel.

    The frontend dialog is the preferred way to bind iLink, but for ops
    who prefer the terminal we expose the same call here. Output is
    JSON; pipe to ``jq`` for readability.
    """
    import json
    import uuid as _uuid

    async def _run() -> None:
        from app.db.session import get_session_factory
        from app.repositories.channel import ChannelRepository
        from app.services.channels._wechat_ilink import start_qr_login

        ch_id = _uuid.UUID(channel_id)
        async with get_session_factory()() as db:
            ch = await ChannelRepository(db).get(ch_id)
            if ch is None:
                console.print(f"[red]channel {channel_id} not found[/red]")
                sys.exit(1)
            qr = await start_qr_login(channel=ch)
            console.print(json.dumps(qr, ensure_ascii=False, indent=2))

    asyncio.run(_run())


if __name__ == "__main__":
    cli()
