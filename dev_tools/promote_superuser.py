# promote_superuser.py — safe CLI promotion tool
"""
Developer tool: promote a user to superuser by email.

Usage:
    python promote_superuser.py user@example.com
"""

import asyncio
import sys

from sqlalchemy import select

from db_async import get_async_session
from models_user import User
import models_user_park  # noqa: F401  # ensure UserParkAccess model is imported/registered


async def promote(email: str) -> None:
    """Promote the user with the given email to superuser."""
    async for session in get_async_session():
        result = await session.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()

        if not user:
            print(f"❌ No user found with email {email}")
            return

        if user.is_superuser:
            print(f"ℹ️ {email} is already a superuser.")
            return

        user.is_superuser = True
        await session.commit()
        print(f"✅ {email} promoted to superuser")


def main(argv: list[str]) -> None:
    if len(argv) != 2:
        print("Usage: python promote_superuser.py <email>")
        return

    email = argv[1]
    asyncio.run(promote(email))


if __name__ == "__main__":
    main(sys.argv)
