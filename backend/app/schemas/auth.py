from __future__ import annotations

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in_minutes: int
    user: "CurrentUserOut"


class RoleOut(ORMModel):
    name: str
    display_name: str


class CurrentUserOut(ORMModel):
    id: int
    username: str
    full_name: str
    email: str
    position_title: str | None = None
    must_change_password: bool = False
    roles: list[RoleOut] = []
    permissions: list[str] = []
    intern_id: int | None = None


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=10, max_length=72)


TokenResponse.model_rebuild()
