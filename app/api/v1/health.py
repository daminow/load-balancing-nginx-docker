from fastapi import APIRouter, status
from sqlalchemy import text

from app.api.deps import SessionDep, SettingsDep

router = APIRouter(tags=["health"])


@router.get("/healthz", status_code=status.HTTP_200_OK, summary="Liveness probe")
async def liveness(settings: SettingsDep) -> dict[str, str]:
    return {"status": "ok", "instance": settings.instance_id}


@router.get("/readyz", status_code=status.HTTP_200_OK, summary="Readiness probe")
async def readiness(session: SessionDep, settings: SettingsDep) -> dict[str, str]:
    await session.execute(text("SELECT 1"))
    return {"status": "ready", "instance": settings.instance_id}
