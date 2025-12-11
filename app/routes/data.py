from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import current_user
from db_async import get_async_session
from models_user import User as DBUser
from models_user_park import UserParkAccess
from parks import PARKS
from app.broadcast import get_plc_clients, executor
from app.config import PLC_CONFIG
from app.telemetry import payload_from_raw_list

router = APIRouter(tags=["data"])


@router.get("/data")
async def get_initial_data(
    user: DBUser = Depends(current_user),
    session: AsyncSession = Depends(get_async_session),
):
    if user.is_superuser:
        allowed_urls = {c["url"] for c in PLC_CONFIG}
    else:
        res = await session.execute(
            select(UserParkAccess.park_id).where(UserParkAccess.user_id == user.id)
        )
        park_ids = [r[0] for r in res.all()]
        allowed_urls = {PARKS[p]["url"] for p in park_ids if p in PARKS}

    clients = get_plc_clients()
    visible_clients = [p for p in clients if p.url in allowed_urls]

    raw = list(executor.map(lambda p: p.read_data(), visible_clients))
    return payload_from_raw_list(raw)
