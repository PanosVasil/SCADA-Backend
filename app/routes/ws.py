import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status

from db_async import get_async_session
from parks import PARKS, user_allowed_urls
from app.auth_helpers import user_from_token
from app.broadcast import active_ws_connections

router = APIRouter()


@router.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    proto = websocket.headers.get("Sec-WebSocket-Protocol") or ""
    token = None

    # -----------------------------
    # Handshake + token retrieval
    # -----------------------------
    try:
        if proto.startswith("bearer,"):
            # Sec-WebSocket-Protocol: bearer,<JWT>
            token = proto.split(",", 1)[1].strip()
            await websocket.accept(subprotocol=proto)
        else:
            # ?token=<JWT>
            token = websocket.query_params.get("token")
            await websocket.accept()
    except Exception as e:
        logging.error(f"WS accept error: {e}")
        return

    if not token:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    # -----------------------------
    # Auth via JWT
    # -----------------------------
    user = await user_from_token(token)
    if not user or not user.is_active:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    # -----------------------------
    # Determine allowed parks/URLs
    # -----------------------------
    async for session in get_async_session():
        if user.is_superuser:
            allowed_urls = None  # unrestricted
        else:
            allowed_urls = await user_allowed_urls(session, user)

    # Attach allowed URLs to the websocket so the broadcast thread can filter
    websocket.allowed_urls = allowed_urls  # type: ignore[attr-defined]

    # Track this connection in the global map
    user_key = str(user.id)
    active_ws_connections.setdefault(user_key, set()).add(websocket)
    logging.info(f"✅ WS connected: {user.email}")

    try:
        # We still don't care about incoming messages, but:
        # - waiting on receive() lets shutdown cancel this task immediately
        # - we handle disconnects cleanly
        while True:
            try:
                msg = await websocket.receive()
                if msg.get("type") == "websocket.disconnect":
                    raise WebSocketDisconnect()
            except WebSocketDisconnect:
                raise
            except asyncio.CancelledError:
                logging.info(f"⚠️ WS receive cancelled for {user.email}")
                raise
            except Exception as e:
                logging.error(f"WS receive error for {user.email}: {e}")
                break

    except (WebSocketDisconnect, asyncio.CancelledError):
        logging.info(f"⚠️ WS disconnected/cancelled: {user.email}")
    finally:
        bucket = active_ws_connections.get(user_key)
        if bucket:
            bucket.discard(websocket)
            if not bucket:
                active_ws_connections.pop(user_key, None)
        logging.info(f"🔌 WS cleanup complete for {user.email}")
