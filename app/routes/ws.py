import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db_async import get_async_session
from models_user_park import UserParkAccess
from parks import PARKS
from app.auth_helpers import user_from_token
from app.broadcast import active_ws_connections
from app.config import PLC_CONFIG

router = APIRouter()


@router.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    proto = websocket.headers.get("Sec-WebSocket-Protocol") or ""
    token = None

    try:
        if proto.startswith("bearer,"):
            token = proto.split(",", 1)[1].strip()
            await websocket.accept(subprotocol=proto)
        else:
            token = websocket.query_params.get("token")
            await websocket.accept()
    except Exception as e:
        logging.error(f"WS accept error: {e}")
        return

    if not token:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    user = await user_from_token(token)
    if not user or not user.is_active:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    # Determine allowed parks for this user
    async for session in get_async_session():  # generator yields exactly one session
        if user.is_superuser:
            allowed_urls = {cfg["url"] for cfg in PLC_CONFIG}
        else:
            res = await session.execute(
                select(UserParkAccess.park_id).where(UserParkAccess.user_id == user.id)
            )
            park_ids = [r[0] for r in res.all()]
            allowed_urls = {PARKS[p]["url"] for p in park_ids if p in PARKS}

    websocket.allowed_urls = allowed_urls
    user_key = str(user.id)
    active_ws_connections.setdefault(user_key, set()).add(websocket)
    logging.info(f"✅ WS connected: {user.email}")

    try:
        # We don't care about incoming messages; we just keep the WS alive
        while True:
            await asyncio.sleep(3600)
    except (WebSocketDisconnect, asyncio.CancelledError):
        logging.info(f"⚠️ WS disconnected/cancelled: {user.email}")
    finally:
        bucket = active_ws_connections.get(user_key)
        if bucket:
            bucket.discard(websocket)
            if not bucket:
                active_ws_connections.pop(user_key, None)
        logging.info(f"🔌 WS cleanup complete for {user.email}")
