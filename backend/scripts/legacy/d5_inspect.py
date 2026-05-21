"""Inspect last 2 chat sessions + their agent metadata + approval rows."""
from __future__ import annotations

import asyncio

from sqlalchemy import select

from app.db.models.agent import Agent
from app.db.models.approval import Approval
from app.db.models.message import Message
from app.db.models.session import Session
from app.db.session import get_session_factory


async def main() -> None:
    factory = get_session_factory()
    async with factory() as db:
        sessions = (
            await db.execute(
                select(Session).order_by(Session.created_at.desc()).limit(2)
            )
        ).scalars().all()
        for s in sessions:
            print(f"Session {s.id}")
            print(f"  subject={s.subject_id} kind={s.kind} title={s.title!r}")
            print(f"  msgs={s.message_count} owner={s.owner_identity_id}")
            if s.subject_id:
                ag = await db.get(Agent, s.subject_id)
                if ag:
                    md = ag.metadata_json or {}
                    print(f"  Agent: name={ag.name!r}")
                    print(f"    sandbox={md.get('sandbox')!r}")
                    print(f"    approvals={md.get('approvals')!r}")
                    print(f"    autonomy={ag.autonomy_level}")
            msgs = (
                await db.execute(
                    select(Message)
                    .where(Message.session_id == s.id)
                    .order_by(Message.created_at)
                )
            ).scalars().all()
            for m in msgs:
                preview = str(m.content_json)[:200]
                tc = bool(m.tool_call_json)
                role = m.role.value if hasattr(m.role, "value") else m.role
                print(f"    msg[{role}] tool_call={tc} text={preview}")
            apr = (
                await db.execute(
                    select(Approval).where(Approval.session_id == s.id)
                )
            ).scalars().all()
            print(f"  approvals for this session: {len(apr)}")
            for a in apr:
                status = a.status.value if hasattr(a.status, "value") else a.status
                print(f"    - tool={a.tool_name} status={status} summary={a.summary!r}")
            print()


if __name__ == "__main__":
    asyncio.run(main())
