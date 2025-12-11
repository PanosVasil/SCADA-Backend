from __future__ import annotations

from typing import List, Optional, Union
from uuid import UUID

from pydantic import BaseModel


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
