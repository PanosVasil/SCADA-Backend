from __future__ import annotations

import logging
import asyncio
import threading

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from auth import (
    fastapi_users,
    auth_backend,
)
from schemas_user import UserRead, UserUpdate
from parks_routes import router as parks_router

from app.config import CORS_ALLOW_ORIGINS
from app.logging_config import configure_logging
from app.broadcast import (
    stop_event,
    executor,
    init_plc_clients,
    data_broadcast_loop,
    disconnect_all_clients,
)
from app.routes.auth_extra import router as auth_extra_router
from app.routes.admin import router as admin_router
from app.routes.me import router as me_router
from app.routes.data import router as data_router
from app.routes.write import router as write_router
from app.routes.ws import router as ws_router

configure_logging()

app = FastAPI(title="SCADA Web API", version="1.0")

_origins_env = CORS_ALLOW_ORIGINS
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

# FastAPI-Users
app.include_router(fastapi_users.get_auth_router(auth_backend), prefix="/auth/jwt", tags=["auth"])
app.include_router(fastapi_users.get_reset_password_router(), prefix="/auth", tags=["auth"])
app.include_router(fastapi_users.get_verify_router(UserRead), prefix="/auth", tags=["auth"])
app.include_router(fastapi_users.get_users_router(UserRead, UserUpdate), prefix="/users", tags=["users"])

# Our routers
app.include_router(auth_extra_router)
app.include_router(admin_router)
app.include_router(me_router)
app.include_router(data_router)
app.include_router(write_router)
app.include_router(ws_router)
app.include_router(parks_router)


@app.on_event("startup")
async def on_startup():
    # Create PLC clients and start broadcast thread
    init_plc_clients()
    loop = asyncio.get_running_loop()
    threading.Thread(target=data_broadcast_loop, args=(loop,), daemon=True).start()
    logging.info("Startup complete.")


@app.on_event("shutdown")
async def on_shutdown():
    logging.info("Shutting down...")

    # Stop broadcast loop + executor
    stop_event.set()
    executor.shutdown(wait=False, cancel_futures=True)

    # Clean + fast-disconnect all PLC clients
    disconnect_all_clients()

    logging.info("Shutdown complete.")
