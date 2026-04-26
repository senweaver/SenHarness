"""Version + build info."""

from __future__ import annotations

from fastapi import APIRouter

from app import __version__
from app.core.config import settings

router = APIRouter()


@router.get("/version", summary="Build version")
async def version() -> dict[str, str]:
    return {"name": settings.APP_NAME, "version": __version__, "env": settings.APP_ENV}
