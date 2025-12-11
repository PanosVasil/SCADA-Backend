from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from auth import current_user
from db_async import get_async_session
from models_user import User as DBUser
from parks import PARKS, user_allowed_urls
from app.broadcast import get_plc_clients, executor
from app.telemetry import payload_from_raw_list

router = APIRouter(tags=["data"])


@router.get("/data")
async def get_initial_data(
    user: DBUser = Depends(current_user),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Return the initial telemetry snapshot for all parks the user is allowed to see.
    - Superuser: all parks from PARKS
    - Normal user: only parks assigned in UserParkAccess (via parks.user_allowed_urls)
    """
    if user.is_superuser:
        # Superuser sees every park defined in config.json / PARKS
        allowed_urls = {info["url"] for info in PARKS.values()}
    else:
        # Normal user: map DB park_ids -> URLs using shared helper
        allowed_urls = await user_allowed_urls(session, user)

    clients = get_plc_clients()
    visible_clients = [p for p in clients if p.url in allowed_urls]

    raw = list(executor.map(lambda p: p.read_data(), visible_clients))
    return payload_from_raw_list(raw)
