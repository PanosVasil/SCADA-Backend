from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import JSONResponse
from fastapi_users.exceptions import UserAlreadyExists
import logging

from auth import get_user_manager
from schemas_user import UserCreate, UserRead

router = APIRouter(tags=["auth"])

@router.post("/auth/register", response_model=UserRead)
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
