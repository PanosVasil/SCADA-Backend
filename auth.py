# auth.py (FastAPI-Users v14 wiring + SMTP Welcome Email + Simple Roles)
from __future__ import annotations

import os
import uuid
import asyncio
import smtplib
from email.message import EmailMessage
from pathlib import Path
from typing import AsyncGenerator, Optional

from dotenv import load_dotenv
from fastapi import Depends
from fastapi_users import FastAPIUsers
from fastapi_users.authentication import AuthenticationBackend, BearerTransport, JWTStrategy
from fastapi_users.manager import BaseUserManager
from fastapi_users_db_sqlalchemy import SQLAlchemyUserDatabase
from sqlalchemy.ext.asyncio import AsyncSession

from db_async import get_async_session
from models_user import User
from schemas_user import UserRead, UserCreate, UserUpdate

# -----------------------------------------------------------------------------
# ENV / SECRETS
# -----------------------------------------------------------------------------
load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env")

SECRET = os.getenv("SECRET_KEY")
if not SECRET:
    raise RuntimeError("Missing SECRET_KEY in environment (.env)")


# SMTP settings (optional welcome email)
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USERNAME = os.getenv("SMTP_USERNAME", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM = os.getenv("SMTP_FROM", SMTP_USERNAME or "no-reply@example.com")
SMTP_USE_TLS = os.getenv("SMTP_USE_TLS", "true").lower() in {"1", "true", "yes"}
WELCOME_EMAIL_ENABLED = os.getenv("WELCOME_EMAIL_ENABLED", "true").lower() in {"1", "true", "yes"}

# -----------------------------------------------------------------------------
# Authentication (JWT over Bearer)
# -----------------------------------------------------------------------------
bearer_transport = BearerTransport(tokenUrl="/auth/jwt/login")

def get_jwt_strategy() -> JWTStrategy:
    lifetime_minutes = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "1440"))
    return JWTStrategy(secret=SECRET, lifetime_seconds=lifetime_minutes * 60)

auth_backend = AuthenticationBackend(
    name="jwt-bearer",
    transport=bearer_transport,
    get_strategy=get_jwt_strategy,
)

# -----------------------------------------------------------------------------
# Database adapter (async SQLAlchemy)
# -----------------------------------------------------------------------------
async def get_user_db(
    session: AsyncSession = Depends(get_async_session),
) -> AsyncGenerator[SQLAlchemyUserDatabase, None]:
    yield SQLAlchemyUserDatabase(session, User)

# -----------------------------------------------------------------------------
# Email helpers
# -----------------------------------------------------------------------------
def _build_welcome_message(to_email: str) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = "Welcome to the SCADA Portal"
    msg["From"] = SMTP_FROM
    msg["To"] = to_email
    msg.set_content(
        f"""Hello,

Your account on the SCADA Portal was created successfully.

You can now sign in with your email address: {to_email}

If you did not request this, please contact the administrator.

— KTL Control Systems
"""
    )
    return msg

def _send_email_sync(message: EmailMessage) -> None:
    # If not configured, do nothing (don’t block registration)
    if not SMTP_HOST or not SMTP_FROM:
        return

    if SMTP_USE_TLS:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as smtp:
            smtp.ehlo()
            smtp.starttls()
            if SMTP_USERNAME and SMTP_PASSWORD:
                smtp.login(SMTP_USERNAME, SMTP_PASSWORD)
            smtp.send_message(message)
    else:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as smtp:
            if SMTP_USERNAME and SMTP_PASSWORD:
                smtp.login(SMTP_USERNAME, SMTP_PASSWORD)
            smtp.send_message(message)

async def send_welcome_email(to_email: str) -> None:
    if not WELCOME_EMAIL_ENABLED:
        return
    try:
        message = _build_welcome_message(to_email)
        await asyncio.to_thread(_send_email_sync, message)
    except Exception:
        # swallow email errors to avoid breaking registration
        pass

# -----------------------------------------------------------------------------
# User manager (v14 requires a BaseUserManager subclass)
# -----------------------------------------------------------------------------
class UserManager(BaseUserManager[User, uuid.UUID]):
    # Required secrets for password reset / email verification features.
    reset_password_token_secret = SECRET
    verification_token_secret = SECRET

    # IMPORTANT: v14 needs this to parse JWT "sub" -> UUID
    def parse_id(self, user_id: str) -> uuid.UUID:
        return uuid.UUID(user_id)

    async def validate_password(self, password: str, user: Optional[User] = None) -> None:
        # No password policy by your requirements
        return None

    async def on_after_register(self, user: User, request=None):
        await send_welcome_email(user.email)

async def get_user_manager(
    user_db=Depends(get_user_db),
) -> AsyncGenerator[UserManager, None]:
    yield UserManager(user_db)

# -----------------------------------------------------------------------------
# FastAPI-Users instance (IMPORTANT: use get_user_manager here)
# -----------------------------------------------------------------------------
fastapi_users = FastAPIUsers[User, uuid.UUID](
    get_user_manager,
    [auth_backend],
)

# -----------------------------------------------------------------------------
# Dependencies for routes
# -----------------------------------------------------------------------------
current_user = fastapi_users.current_user(active=True)
current_superuser = fastapi_users.current_user(active=True, superuser=True)
