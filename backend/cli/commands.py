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
    """Run uvicorn."""
    uvicorn.run(
        "app.main:app",
        host=host or settings.BACKEND_HOST,
        port=port or settings.BACKEND_PORT,
        reload=reload if reload is not None else settings.APP_DEBUG,
        access_log=settings.APP_DEBUG,
    )


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
    """Seed demo workspace + default agent."""

    async def _run() -> None:
        from app.db.session import get_session_factory
        from app.services.seed import seed_defaults

        async with get_session_factory()() as session:
            summary = await seed_defaults(session)
            await session.commit()
            for line in summary:
                console.print(line)

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


if __name__ == "__main__":
    cli()
