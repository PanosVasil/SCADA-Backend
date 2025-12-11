from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from auth import current_superuser
from db_async import get_async_session
from models_user import User as DBUser
from app.schemas import AdminUserSummary

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/ping")
async def admin_ping(_: DBUser = Depends(current_superuser)):
    return {"ok": True}


@router.get("/users", response_model=List[AdminUserSummary])
async def list_users(
    q: Optional[str] = Query(None, description="Search by email (contains, case-insensitive)"),
    is_superuser: Optional[bool] = Query(None),
    is_active: Optional[bool] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _: DBUser = Depends(current_superuser),
    session: AsyncSession = Depends(get_async_session),
):
    stmt = select(DBUser)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(func.lower(DBUser.email).like(func.lower(like)))
    if is_superuser is not None:
        stmt = stmt.where(DBUser.is_superuser == is_superuser)
    if is_active is not None:
        stmt = stmt.where(DBUser.is_active == is_active)
    stmt = stmt.order_by(DBUser.email).limit(limit).offset(offset)

    result = await session.execute(stmt)
    users = result.scalars().all()

    return [
        AdminUserSummary(
            id=u.id,
            email=u.email,
            is_superuser=bool(u.is_superuser),
            is_active=bool(u.is_active),
            organization_id=u.organization_id,
            default_park_id=u.default_park_id,
        )
        for u in users
    ]
