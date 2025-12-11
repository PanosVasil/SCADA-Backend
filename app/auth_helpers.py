from __future__ import annotations

import logging
from typing import Optional
from uuid import UUID

import jwt
from sqlalchemy import select

from app.config import SECRET_KEY
from db_async import get_async_session
from models_user import User as DBUser


SECRET = SECRET_KEY
JWT_ALG = "HS256"
JWT_AUD = "fastapi-users:auth"


async def get_user_by_id(user_id: str) -> Optional[DBUser]:
    try:
        uuid_id = UUID(user_id)
    except Exception:
        return None

    async for session in get_async_session():
        res = await session.execute(select(DBUser).where(DBUser.id == uuid_id))
        return res.scalar_one_or_none()


def _decode_jwt(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, SECRET, algorithms=[JWT_ALG], audience=JWT_AUD)
    except Exception as e:
        logging.error(f"JWT decode failed: {e}")
        return None


async def user_from_token(token: str) -> Optional[DBUser]:
    payload = _decode_jwt(token)
    if not payload:
        return None
    sub = payload.get("sub")
    if not sub:
        return None
    return await get_user_by_id(sub)
