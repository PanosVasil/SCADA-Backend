# parks_routes.py — admin endpoints for park assignments
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from uuid import UUID
from typing import List

from models_user import User
from models_user_park import UserParkAccess
from db_async import get_async_session
from auth import current_superuser
from parks import PARKS   # ← dict { park_id: {name, url} }

from pydantic import BaseModel

router = APIRouter(prefix="/admin", tags=["admin"])

# ------------------------
# Pydantic Model Returned
# ------------------------
class ParkOut(BaseModel):
    id: str
    name: str
    url: str


# ------------------------
# List all parks
# ------------------------
@router.get("/parks", response_model=List[ParkOut])
async def list_parks(_: User = Depends(current_superuser)):
    return [
        ParkOut(id=pid, name=data["name"], url=data["url"])
        for pid, data in PARKS.items()
    ]


# ------------------------
# List all parks assigned to a user
# ------------------------
@router.get("/users/{user_id}/parks", response_model=List[ParkOut])
async def get_user_parks(
    user_id: UUID,
    _: User = Depends(current_superuser),
    session: AsyncSession = Depends(get_async_session),
):
    res = await session.execute(
        select(UserParkAccess.park_id).where(UserParkAccess.user_id == user_id)
    )
    ids = [r[0] for r in res.all()]

    # only return parks that still exist in config.json
    return [
        ParkOut(id=pid, name=PARKS[pid]["name"], url=PARKS[pid]["url"])
        for pid in ids
        if pid in PARKS
    ]


# ------------------------
# Grant park access
# ------------------------
@router.post(
    "/users/{user_id}/parks/{park_id}",
    status_code=status.HTTP_204_NO_CONTENT
)
async def grant_user_park(
    user_id: UUID,
    park_id: str,
    _: User = Depends(current_superuser),
    session: AsyncSession = Depends(get_async_session),
):
    if park_id not in PARKS:
        raise HTTPException(404, f"Unknown park id: {park_id}")

    exists = await session.execute(
        select(UserParkAccess).where(
            UserParkAccess.user_id == user_id,
            UserParkAccess.park_id == park_id
        )
    )
    if exists.scalar_one_or_none() is None:
        session.add(UserParkAccess(user_id=user_id, park_id=park_id))
        await session.commit()


# ------------------------
# Revoke park access
# ------------------------
@router.delete(
    "/users/{user_id}/parks/{park_id}",
    status_code=status.HTTP_204_NO_CONTENT
)
async def revoke_user_park(
    user_id: UUID,
    park_id: str,
    _: User = Depends(current_superuser),
    session: AsyncSession = Depends(get_async_session),
):
    await session.execute(
        delete(UserParkAccess).where(
            UserParkAccess.user_id == user_id,
            UserParkAccess.park_id == park_id
        )
    )
    await session.commit()
