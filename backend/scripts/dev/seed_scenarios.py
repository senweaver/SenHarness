#!/usr/bin/env python
"""Seed three ready-to-use agent scenarios into a workspace.

Idempotent. Running twice adds nothing the second time.

Scenarios:

    * 周报生成器 / Weekly-Report Drafter — drafts a Monday weekly
      report from the user's past-week session history + todo list.
    * 内部知识问答 / Internal Knowledge Q&A — answers employee
      questions against the workspace knowledge base with explicit
      source citation.
    * 客户支持分诊 / Customer-Support Triage — classifies inbound
      customer messages, extracts intent + urgency, and drafts a
      first-line reply for human review.

Each scenario illustrates which Harness layer is doing the heavy
lifting (see docs/harness-engineering.md). The comments on the
scenario dict are where that mapping is explicit — keep them accurate
so new employees reading the seed understand WHY each knob is set.

Usage:

    docker compose exec backend python -m scripts.dev.seed_scenarios
    # OR target a specific workspace slug:
    docker compose exec backend python -m scripts.dev.seed_scenarios \
        --workspace-slug acme

The command expects to be run inside the backend container (or against
a configured .env — it uses the normal app.db.session factory).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from typing import Any

from rich.console import Console

console = Console()


# ─── Scenario definitions ─────────────────────────────────────
# Each scenario maps to an Agent we create in the target workspace.
# The ``_harness_layers`` comment is for docs only; it's not
# persisted — it tells operators which L-layers drive the behaviour.

SCENARIOS: list[dict[str, Any]] = [
    {
        "name": "周报生成器 · Weekly Report Drafter",
        "description": (
            "Reviews the author's past-week session history and drafts a "
            "structured weekly report. Employees run it every Monday and "
            "edit the draft instead of writing from scratch."
        ),
        "persona_md": (
            "You are a concise weekly-report drafter.\n\n"
            "Output format, always:\n"
            "  ## This week\n"
            "    - <concrete accomplishments, 3-6 bullets>\n"
            "  ## Next week\n"
            "    - <planned actions, 3-5 bullets>\n"
            "  ## Blockers\n"
            "    - <risks or open questions, 0-3 bullets>\n\n"
            "Pull facts from the user's recent sessions and todos. "
            "Never invent metrics; if a number isn't available, say "
            "'(metric not yet captured)'."
        ),
        "autonomy": "l2",  # may read memory + session history
        "metadata": {
            # L4 memory: lean heavily on episodic recall of past sessions
            # so the report reflects what the user actually did.
            "context": {"recall_top_k": 10, "recall_min_score": 0.25},
            "todos": True,
        },
        # _harness_layers: L1 (strict output format) + L4 (episodic
        # memory) + L5 (no-invent guardrail)
    },
    {
        "name": "内部知识问答 · Internal Knowledge Q&A",
        "description": (
            "Answers employee questions against the workspace knowledge "
            "base. Always cites source documents; refuses rather than "
            "guesses when no source matches."
        ),
        "persona_md": (
            "You answer employees' questions about internal policies, "
            "playbooks, and product knowledge.\n\n"
            "Rules (non-negotiable):\n"
            "  1. Call the knowledge_search tool at least once before "
            "     drafting an answer.\n"
            "  2. Cite every claim with the source doc title in "
            "     [brackets].\n"
            "  3. If no source matches, say so — 'I couldn't find that "
            "     in the knowledge base; please confirm with <owner>' "
            "     — do not improvise."
        ),
        "autonomy": "l2",
        "metadata": {
            # L2 tools: tools are constrained to retrieval + reading;
            # no shell / write access.
            "toolbox": ["knowledge_search", "web_fetch"],
            "context": {"recall_top_k": 8},
        },
        # _harness_layers: L2 (tool allowlist) + L5 (source-citation
        # validator) + L6 (refusal policy)
    },
    {
        "name": "客户支持分诊 · Customer-Support Triage",
        "description": (
            "Ingests customer messages, classifies intent + urgency, "
            "and drafts a first-line reply. Destructive actions (refund, "
            "account suspend) require human approval."
        ),
        "persona_md": (
            "You triage inbound customer messages.\n\n"
            "For every input, produce:\n"
            "  - intent: one of {billing, bug_report, feature_request, "
            "    complaint, praise, other}\n"
            "  - urgency: one of {low, medium, high, critical}\n"
            "  - suggested_reply: a draft response the human will "
            "    approve before sending.\n\n"
            "If the reply would commit to a refund, suspension, or "
            "contract change, request approval explicitly."
        ),
        "autonomy": "l3",  # destructive tools → HITL approval
        "metadata": {
            "approvals": "required_for_destructive",
            "shields": {
                "pii_redaction": True,
                "injection_detection": True,
            },
        },
        # _harness_layers: L3 (structured output) + L6 (HITL approval
        # on destructive actions) + L6 (PII + injection shields)
    },
]


async def seed(workspace_slug: str | None) -> None:
    """Create the three scenario agents in the target workspace."""
    from app.db.models.agent import AgentVisibility, AutonomyLevel
    from app.db.session import get_session_factory
    from app.repositories.agent import AgentRepository
    from app.repositories.workspace import WorkspaceRepository
    from app.services import agent as agent_svc

    factory = get_session_factory()
    async with factory() as db:
        ws_repo = WorkspaceRepository(db)
        ws = None
        if workspace_slug:
            ws = await ws_repo.get_by_slug(workspace_slug)
            if ws is None:
                console.print(f"[red]workspace slug={workspace_slug!r} not found[/red]")
                sys.exit(2)
        else:
            # Fall back to the demo workspace `seed_defaults` creates.
            ws = await ws_repo.get_by_slug("demo")
            if ws is None:
                console.print(
                    "[red]no demo workspace; run `cli.commands seed` first "
                    "or pass --workspace-slug[/red]"
                )
                sys.exit(2)

        console.print(f"[cyan]seeding into workspace[/cyan] {ws.name!r} ({ws.slug})")
        agent_repo = AgentRepository(db)
        existing = {
            a.name
            for a in await agent_repo.list_visible(
                workspace_id=ws.id, identity_id=None, offset=0, limit=500
            )
        }

        created = 0
        skipped = 0
        for spec in SCENARIOS:
            if spec["name"] in existing:
                console.print(f"[dim]= already present: {spec['name']}[/dim]")
                skipped += 1
                continue

            autonomy_map = {
                "l1": AutonomyLevel.L1,
                "l2": AutonomyLevel.L2,
                "l3": AutonomyLevel.L3,
            }
            await agent_svc.create_agent(
                db,
                workspace_id=ws.id,
                created_by=None,
                name=spec["name"],
                description=spec["description"],
                persona_md=spec["persona_md"],
                autonomy_level=autonomy_map[spec["autonomy"]],
                visibility=AgentVisibility.WORKSPACE,
                metadata_json=spec["metadata"],
            )
            console.print(f"[green]+ agent created:[/green] {spec['name']}")
            created += 1

        await db.commit()
        console.print(f"[bold green]done[/bold green] — {created} created, {skipped} skipped")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Seed three ready-to-use scenario agents into a workspace."
    )
    parser.add_argument(
        "--workspace-slug",
        help=("Target workspace slug. Defaults to 'demo' (created by cli.commands seed)."),
        default=None,
    )
    args = parser.parse_args()
    asyncio.run(seed(args.workspace_slug))


if __name__ == "__main__":
    main()
