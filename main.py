# main.py — SCADA Web API + FastAPI-Users v14.x
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Union
from uuid import UUID
import jwt  # PyJWT

# --- Parks & Access ---
from parks_routes import router as parks_router
from models_user_park import UserParkAccess
from parks import PARKS

# --- FastAPI Core ---
from dotenv import load_dotenv
from fastapi import (
    Body,
    Depends,
    FastAPI,
    HTTPException,
    WebSocket,
    WebSocketDisconnect,
    status,
    Query,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi_users.exceptions import UserAlreadyExists
from pydantic import BaseModel

# --- OPC UA ---
from opcua import ua, Client
from opcua.ua.uaerrors import UaStatusCodeError

# --- Auth / DB pieces ---
from auth import (
    fastapi_users,
    auth_backend,
    current_user,
    current_superuser,
    get_jwt_strategy,
    get_user_manager,
)
from schemas_user import UserRead, UserCreate, UserUpdate
from models_user import User as DBUser
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from db_async import get_async_session

# ---------------------------------------------------------------------
# CONFIG / ENV
# ---------------------------------------------------------------------
ROOT_DIR = Path(__file__).resolve().parent
load_dotenv(dotenv_path=ROOT_DIR / ".env")

try:
    with open(ROOT_DIR / "config.json", "r", encoding="utf-8") as f:
        config = json.load(f)
    PLC_CONFIG = config["plc_config"]
    COMMON_ROOT_NODE_ID = config["common_root_node_id"]
    BROADCAST_INTERVAL_SECONDS = float(config["broadcast_interval_seconds"])
    PLC_RECONNECT_DELAY_MINUTES = int(config["plc_reconnect_delay_minutes"])
except Exception as e:
    raise RuntimeError(f"Failed to load config.json: {e}")


# ---------------------------------------------------------------------
# APP + LOGGING + CORS
# ---------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(threadName)s - %(levelname)s - %(message)s",
)
logging.getLogger("opcua").setLevel(logging.WARNING)

app = FastAPI(title="SCADA Web API", version="1.0")

# ✅ CORS from .env (fallbacks include Vite/CRA common ports)
_origins_env = os.getenv("CORS_ALLOW_ORIGINS", "")
origins = [o.strip() for o in _origins_env.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins or [
        "http://127.0.0.1:3000",
        "http://localhost:3000",
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "http://127.0.0.1:8080",
        "http://localhost:8080",
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------
# FASTAPI-USERS ROUTERS (v14 compliant)
# ---------------------------------------------------------------------
app.include_router(fastapi_users.get_auth_router(auth_backend), prefix="/auth/jwt", tags=["auth"])
app.include_router(fastapi_users.get_reset_password_router(), prefix="/auth", tags=["auth"])
app.include_router(fastapi_users.get_verify_router(UserRead), prefix="/auth", tags=["auth"])
app.include_router(fastapi_users.get_users_router(UserRead, UserUpdate), prefix="/users", tags=["users"])
app.include_router(parks_router)

# ---------------------------------------------------------------------
# CUSTOM REGISTRATION
# ---------------------------------------------------------------------
@app.post("/auth/register", response_model=UserRead, tags=["auth"])
async def custom_register(
    user_create: UserCreate = Body(...),
    manager=Depends(get_user_manager),
):
    try:
        created_user = await manager.create(user_create)
        return created_user
    except UserAlreadyExists:
        return JSONResponse(status_code=400, content={"detail": "User already exists."})
    except Exception as e:
        logging.error(f"Registration failed: {e}")
        raise HTTPException(status_code=500, detail="Registration failed.")

# ---------------------------------------------------------------------
# MODELS / TYPES
# ---------------------------------------------------------------------
class APICurrentUser(BaseModel):
    id: str
    email: str
    organization_id: Optional[str] = None
    default_park_id: Optional[str] = None
    is_superuser: bool
    is_active: bool

class WriteRequest(BaseModel):
    plc_url: str
    node_name: str
    value: Union[float, List[bool], int, str]

class AdminUserSummary(BaseModel):
    id: UUID
    email: str
    is_superuser: bool
    is_active: bool
    organization_id: Optional[str] = None
    default_park_id: Optional[str] = None

# ---------------------------------------------------------------------
# OPC UA CLIENT
# ---------------------------------------------------------------------
class ConnectionStatus(str, Enum):
    CONNECTED = "CONNECTED"
    CONNECTING = "CONNECTING"
    DISCONNECTED = "DISCONNECTED"
    ERROR = "ERROR"

class OpcUaClient:
    def __init__(self, url: str, custom_name: str, root_node_id: str):
        self.url = url
        self.name = custom_name
        self.server_name = ""
        self.root_node_id = root_node_id
        self.client: Optional[Client] = None
        self.nodes: Dict[str, Any] = {}
        self.status = ConnectionStatus.DISCONNECTED
        self.last_reconnect_attempt: Optional[datetime] = None

    def _get_readable_nodes(self, node) -> dict:
        nodes_dict: Dict[str, Any] = {}
        try:
            if node.get_node_class() == ua.NodeClass.Variable:
                nodes_dict[node.get_browse_name().Name] = node
        except Exception:
            pass
        try:
            for child in node.get_children():
                nodes_dict.update(self._get_readable_nodes(child))
        except Exception:
            pass
        return nodes_dict

    def connect_and_discover(self) -> bool:
        self.last_reconnect_attempt = datetime.now()

        # Cleanly disconnect existing client
        if self.client:
            try:
                self.client.disconnect()
            except Exception:
                pass

        self.client = Client(self.url, timeout=40)
        self.status = ConnectionStatus.CONNECTING
        self.nodes = {}

        logging.info(f"{self.name}: 🔄 Connecting...")

        try:
            self.client.connect()

            # Optional metadata read
            try:
                self.server_name = (
                    self.client.get_node("ns=0;i=2254").get_value() or ""
                )
            except Exception:
                self.server_name = ""

            # 🔥 ALWAYS rebuild node map after connecting (fixes stale node handles)
            root_node = self.client.get_node(self.root_node_id)
            self.nodes = self._get_readable_nodes(root_node)
            logging.info(
                f"{self.name}: 🔁 Node map rebuilt ({len(self.nodes)} nodes)"
            )

            self.status = ConnectionStatus.CONNECTED
            logging.info(f"{self.name}: ✅ CONNECTED")

            return True

        except Exception as e:
            self.status = ConnectionStatus.DISCONNECTED
            logging.error(f"{self.name}: ❌ Connection error: {e}")
            self.client = None
            return False


    def read_data(self) -> Dict[str, Any]:
        data = {"name": self.name, "status": self.status.value, "url": self.url, "nodes": {}}
        if self.status != ConnectionStatus.CONNECTED or not self.client:
            return data
        try:
            ids = list(self.nodes.values())
            names = list(self.nodes.keys())
            if not ids:
                data["error"] = "No readable nodes."
                return data
            values = self.client.get_values(ids)
            for n, v in zip(names, values):
                data.setdefault("nodes", {})
                data["nodes"][n] = v
        except UaStatusCodeError as e:
            self.status = ConnectionStatus.ERROR
            data["error"] = f"OPC UA read error: {e}"
        except Exception:
            data["error"] = "Temporary read failure."
        return data

# ---------------------------------------------------------------------
# AUTH HELPERS
# ---------------------------------------------------------------------
SECRET = os.getenv("SECRET_KEY") or "change-me"
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
        # FastAPI-Users tokens include aud=["fastapi-users:auth"]
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

# ---------------------------------------------------------------------
# TELEMETRY SHAPE HELPERS (canonical shape for REST + WS)
# ---------------------------------------------------------------------
def _dict_client_to_view(d: Dict[str, Any]) -> Dict[str, Any]:
    nodes = d.get("nodes") or {}
    nodes_list = [{"name": k, "value": str(v)} for k, v in nodes.items()]
    return {
        "name": d.get("name") or "",
        "url": d.get("url") or "",
        "status": d.get("status") or "DISCONNECTED",
        "nodes": nodes_list,
    }

def _payload_from_raw_list(raw_list: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {"plc_clients": [_dict_client_to_view(d) for d in raw_list]}

# ---------------------------------------------------------------------
# THREADING & BROADCAST LOOP
# ---------------------------------------------------------------------
stop_event = threading.Event()
plc_clients: List[OpcUaClient] = []
active_ws_connections: Dict[str, Set[WebSocket]] = {}
executor = ThreadPoolExecutor(max_workers=max(len(PLC_CONFIG) * 2, 2))

def data_broadcast_loop(loop: asyncio.AbstractEventLoop):
    reconnect_delay = timedelta(minutes=PLC_RECONNECT_DELAY_MINUTES)
    logging.info("Background broadcast started.")
    while not stop_event.is_set():
        try:
            # reconnect any dropped/error clients
            reconnect_list = [
                p for p in plc_clients
                if p.status in (ConnectionStatus.DISCONNECTED, ConnectionStatus.ERROR)
                and (not p.last_reconnect_attempt or (datetime.now() - p.last_reconnect_attempt) > reconnect_delay)
            ]
            if reconnect_list:
                list(executor.map(lambda p: p.connect_and_discover(), reconnect_list))

            # read all data once per tick
            all_plc_data = list(executor.map(lambda p: p.read_data(), plc_clients))

            # ✅ Broadcast filtered data to each websocket in one canonical shape
            for user_id, sockets in list(active_ws_connections.items()):
                for ws in list(sockets):
                    try:
                        allowed = getattr(ws, "allowed_urls", None)
                        if allowed:
                            visible_raw = [d for d in all_plc_data if d.get("url") in allowed]
                        else:
                            visible_raw = all_plc_data

                        # Convert each raw OPC block into canonical frontend shape
                        visible_payload = _payload_from_raw_list(visible_raw)

                        payload = {
                            "type": "telemetry_update",
                            "data": visible_payload
                        }
                        asyncio.run_coroutine_threadsafe(ws.send_json(payload), loop)
                    except Exception as e:
                        logging.error(f"WebSocket send error for {user_id}: {e}")

            # sleep until next tick
            for _ in range(int(BROADCAST_INTERVAL_SECONDS)):
                if stop_event.is_set():
                    break
                time.sleep(1)

        except Exception as e:
            logging.error(f"Broadcast error: {e}")
            time.sleep(5)
    logging.info("Broadcast stopped.")

# ---------------------------------------------------------------------
# LIFESPAN
# ---------------------------------------------------------------------
@app.on_event("startup")
async def on_startup():
    for cfg in PLC_CONFIG:
        plc_clients.append(OpcUaClient(cfg["url"], cfg["name"], COMMON_ROOT_NODE_ID))
    loop = asyncio.get_running_loop()
    threading.Thread(target=data_broadcast_loop, args=(loop,), daemon=True).start()
    logging.info("Startup complete.")

@app.on_event("shutdown")
async def on_shutdown():
    logging.info("Shutting down...")
    stop_event.set()
    executor.shutdown(wait=False, cancel_futures=True)
    for p in plc_clients:
        try:
            if p.client:
                p.client.disconnect()
        except Exception:
            pass
    logging.info("Shutdown complete.")

# ---------------------------------------------------------------------
# ROUTES
# ---------------------------------------------------------------------
@app.get("/me", response_model=APICurrentUser)
async def who_am_i(user: DBUser = Depends(current_user)):
    return APICurrentUser(
        id=str(user.id),
        email=user.email,
        organization_id=user.organization_id,
        default_park_id=user.default_park_id,
        is_superuser=user.is_superuser,
        is_active=user.is_active,
    )

@app.get("/admin/ping")
async def admin_ping(_: DBUser = Depends(current_superuser)):
    return {"ok": True}

@app.get("/admin/users", response_model=List[AdminUserSummary], tags=["admin"])
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

@app.post("/write_value")
async def write_plc_value(
    req: WriteRequest,
    user: DBUser = Depends(current_user),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Generic write endpoint.

    - Scalar values (numbers / bools / strings) are written directly to the node
      with name == req.node_name (this is what the setpoint inputs use).
    - For CMD_Instant_Cutoff we support a special array write:
        value = [True, False]  -> bit0=False, bit1=True  (park OFF or ON depending on your mapping)
      and we resolve the correct child nodes [0], [1] under the array node.
    """

    # ------------------------------------------------------------------
    # 1) PERMISSIONS: superuser OR user with access to this park
    # ------------------------------------------------------------------
    if user.is_superuser:
        allowed_urls = {cfg["url"] for cfg in PLC_CONFIG}
    else:
        res = await session.execute(
            select(UserParkAccess.park_id).where(UserParkAccess.user_id == user.id)
        )
        park_ids = [r[0] for r in res.all()]
        allowed_urls = {PARKS[p]["url"] for p in park_ids if p in PARKS}

    if req.plc_url not in allowed_urls:
        raise HTTPException(403, "You do not have write access to this park.")

    # ------------------------------------------------------------------
    # 2) Resolve target PLC client
    # ------------------------------------------------------------------
    target = next((p for p in plc_clients if p.url == req.plc_url), None)
    if not target or target.status != ConnectionStatus.CONNECTED:
        raise HTTPException(404, "PLC not connected.")

    # ------------------------------------------------------------------
    # 3) SPECIAL CASE: CMD_Instant_Cutoff as 2-bit boolean array
    # ------------------------------------------------------------------
    if isinstance(req.value, list) and req.node_name == "CMD_Instant_Cutoff":
        parent = target.nodes.get("CMD_Instant_Cutoff")
        if not parent:
            logging.error("Array parent node 'CMD_Instant_Cutoff' not found.")
            raise HTTPException(404, "Array node 'CMD_Instant_Cutoff' not found.")

        try:
            children = parent.get_children()
        except Exception as e:
            logging.error(f"Failed to get children for CMD_Instant_Cutoff: {e}")
            raise HTTPException(500, "Failed to resolve cutoff child nodes.")

        # Build index -> child-node map for [0], [1] under THIS array
        index_map: dict[int, Any] = {}
        for child in children:
            try:
                bn = child.get_browse_name().Name  # e.g. "[0]", "[1]"
                if bn.startswith("[") and bn.endswith("]"):
                    idx = int(bn[1:-1])
                    index_map[idx] = child
            except Exception:
                continue

        if not index_map:
            logging.error("No [index] children found under CMD_Instant_Cutoff.")
            raise HTTPException(404, "Cutoff child bits not found.")

        # Write each bit to its proper child
        for idx, bit in enumerate(req.value):
            child = index_map.get(idx)
            if child is None:
                logging.error(f"Child index [{idx}] not found under CMD_Instant_Cutoff.")
                raise HTTPException(404, f"Cutoff bit [{idx}] not found.")

            try:
                dv = ua.DataValue(ua.Variant(bool(bit), ua.VariantType.Boolean))
                child.set_attribute(ua.AttributeIds.Value, dv)
                logging.info(f"Write {bit} to 'CMD_Instant_Cutoff[{idx}]' on {target.name}")
            except Exception as e:
                logging.error(f"Write failed on CMD_Instant_Cutoff[{idx}]: {e}")
                raise HTTPException(500, f"Write failed on cutoff bit [{idx}]: {e}")

        return {"status": "success", "written": req.value}

    # For any other list value we don't support array writes yet
    if isinstance(req.value, list):
        raise HTTPException(
            400,
            "Array writes are only supported for 'CMD_Instant_Cutoff' at the moment.",
        )

    # ------------------------------------------------------------------
    # 4) NORMAL SCALAR WRITE (setpoints etc.)
    # ------------------------------------------------------------------
    node = target.nodes.get(req.node_name)
    if not node:
        raise HTTPException(404, f"Node '{req.node_name}' not found.")

    try:
        vt = node.get_data_type_as_variant_type()
        v: Any = req.value

        # Coerce Python type based on OPC UA data type
        if vt == ua.VariantType.Boolean:
            v = bool(v)
        elif vt in (
            ua.VariantType.Int16,
            ua.VariantType.Int32,
            ua.VariantType.Int64,
            ua.VariantType.UInt16,
            ua.VariantType.UInt32,
            ua.VariantType.UInt64,
        ):
            v = int(v)
        elif vt in (ua.VariantType.Float, ua.VariantType.Double):
            v = float(v)
        # strings etc. pass through

        # --- DEBUG: read before write (best-effort, does not affect behavior) ---
        try:
            before = node.get_value()
            logging.info(
                f"[WRITE DEBUG] Before write '{req.node_name}' on {target.name}: {before!r} (type={type(before).__name__}, vt={vt})"
            )
        except Exception as e:
            logging.debug(f"[WRITE DEBUG] Could not read 'before' value for {req.node_name}: {e}")

        dv = ua.DataValue(ua.Variant(v, vt))
        node.set_attribute(ua.AttributeIds.Value, dv)
        logging.info(f"Write {v} to '{req.node_name}' on {target.name}")

        # --- DEBUG: read after write (best-effort) ---
        try:
            after = node.get_value()
            logging.info(
                f"[WRITE DEBUG] After write '{req.node_name}' on {target.name}: {after!r} (type={type(after).__name__})"
            )
            if after != v:
                logging.warning(
                    f"[WRITE DEBUG] MISMATCH for '{req.node_name}' on {target.name}: wrote {v!r}, server reports {after!r}"
                )
        except Exception as e:
            logging.debug(f"[WRITE DEBUG] Could not read 'after' value for {req.node_name}: {e}")

        return {"status": "success"}

    except Exception as e:
        logging.error(f"Write failed: {e}")
        raise HTTPException(500, f"Write failed.")



# ---------------------------------------------------------------------
# DATA (REST) — same canonical shape as WS
# ---------------------------------------------------------------------
@app.get("/data")
async def get_initial_data(
    user: DBUser = Depends(current_user),
    session: AsyncSession = Depends(get_async_session),
):
    if user.is_superuser:
        allowed_urls = {cfg["url"] for cfg in PLC_CONFIG}
    else:
        res = await session.execute(
            select(UserParkAccess.park_id).where(UserParkAccess.user_id == user.id)
        )
        park_ids = [r[0] for r in res.all()]
        allowed_urls = {PARKS[p]["url"] for p in park_ids if p in PARKS}

    visible_clients = [p for p in plc_clients if p.url in allowed_urls]
    # Read fresh values for the visible list
    raw = list(executor.map(lambda p: p.read_data(), visible_clients))
    return _payload_from_raw_list(raw)

# ---------------------------------------------------------------------
# WEBSOCKET — token via query (?token=...) or Sec-WebSocket-Protocol: bearer,<JWT>
# ---------------------------------------------------------------------
@app.websocket("/ws")
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

    # determine allowed parks
    async for session in get_async_session():
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
        # Keep open; broadcast loop pushes telemetry
        while True:
            await asyncio.sleep(3600)
    except WebSocketDisconnect:
        logging.info(f"⚠️ WS disconnected: {user.email}")
    finally:
        bucket = active_ws_connections.get(user_key)
        if bucket:
            bucket.discard(websocket)
            if not bucket:
                active_ws_connections.pop(user_key, None)
        logging.info(f"🔌 WS cleanup complete for {user.email}")
