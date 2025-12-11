from fastapi import APIRouter, Depends

from auth import current_user
from models_user import User as DBUser
from app.schemas import APICurrentUser

router = APIRouter(tags=["me"])

@router.get("/me", response_model=APICurrentUser)
async def who_am_i(user: DBUser = Depends(current_user)):
    return APICurrentUser(
        id=str(user.id),
        email=user.email,
        organization_id=user.organization_id,
        default_park_id=user.default_park_id,
        is_superuser=user.is_superuser,
        is_active=user.is_active,
    )
