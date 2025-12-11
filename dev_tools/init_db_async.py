# init_db_async.py
"""
Developer tool: initialize the database schema.

Usage:
    python init_db_async.py
"""

import asyncio

from db_async import engine, Base  # uses same engine & Base as the app
# Import models so their tables are registered on Base.metadata
from models_user import User  # noqa: F401
from models_user_park import UserParkAccess  # noqa: F401


async def main() -> None:
    """Create all tables defined on Base.metadata."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("✅ Tables created.")


if __name__ == "__main__":
    asyncio.run(main())
