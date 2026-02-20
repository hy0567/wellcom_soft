"""Pydantic 모델 (요청/응답 스키마)"""
from typing import Optional, List
from pydantic import BaseModel


# === Auth ===
class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    token: str
    user: "UserInfo"


class UserInfo(BaseModel):
    id: int
    username: str
    role: str
    display_name: Optional[str] = None


# === Users ===
class UserCreate(BaseModel):
    username: str
    password: str
    role: str = "user"
    display_name: Optional[str] = None


class UserUpdate(BaseModel):
    display_name: Optional[str] = None
    role: Optional[str] = None
    is_active: Optional[bool] = None
    password: Optional[str] = None


class UserResponse(BaseModel):
    id: int
    username: str
    role: str
    display_name: Optional[str] = None
    is_active: bool
    created_at: Optional[str] = None
    last_login: Optional[str] = None


# === Agents (원격 PC) ===
class AgentRegister(BaseModel):
    agent_id: str           # hostname 또는 UUID
    hostname: str
    os_info: str = ""
    ip: str = ""
    mac_address: str = ""
    screen_width: int = 1920
    screen_height: int = 1080


class AgentHeartbeat(BaseModel):
    agent_id: str
    ip: str = ""
    screen_width: int = 1920
    screen_height: int = 1080


class AgentResponse(BaseModel):
    id: int
    agent_id: str
    hostname: str
    os_info: str = ""
    ip: str = ""
    mac_address: str = ""
    screen_width: int = 1920
    screen_height: int = 1080
    group_name: str = "default"
    display_name: Optional[str] = None
    is_online: bool = False
    owner_id: int = 0
    owner_username: str = ""
    last_seen: Optional[str] = None


# === Groups ===
class GroupCreate(BaseModel):
    name: str
    description: Optional[str] = None


class GroupResponse(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    owner_id: Optional[int] = None
