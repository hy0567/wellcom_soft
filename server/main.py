"""
WellcomSOFT API 서버
FastAPI + MySQL + JWT 인증

핵심 흐름:
1. 에이전트(대상PC)가 서버에 로그인 → JWT 토큰 획득
2. 에이전트가 /api/agents/register로 자신을 등록 (owner_id = 로그인 사용자)
3. 에이전트가 /api/agents/heartbeat로 주기적으로 상태 보고
4. 매니저(관리PC)가 같은 계정으로 로그인 → /api/agents로 해당 사용자의 에이전트 목록 조회
5. 매니저가 에이전트의 IP를 알아내서 WebSocket 직접 연결
"""
import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, Depends, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from auth import (
    hash_password, verify_password, create_token,
    get_current_user, require_admin,
)
from database import get_db
from models import (
    LoginRequest, LoginResponse, UserInfo,
    UserCreate, UserUpdate, UserResponse,
    AgentRegister, AgentHeartbeat, AgentResponse,
    GroupCreate, GroupResponse,
)

app = FastAPI(title="WellcomSOFT API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ===========================================================
# 시작 시 테이블 초기화
# ===========================================================
@app.on_event("startup")
def startup_init():
    with get_db() as conn:
        with conn.cursor() as cur:
            # users 테이블
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    username VARCHAR(50) UNIQUE NOT NULL,
                    password VARCHAR(255) NOT NULL,
                    role VARCHAR(20) DEFAULT 'user',
                    display_name VARCHAR(100),
                    is_active BOOLEAN DEFAULT TRUE,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    last_login DATETIME
                )
            """)

            # agents 테이블 (에이전트 = 원격 PC)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS agents (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    agent_id VARCHAR(255) NOT NULL,
                    owner_id INT NOT NULL,
                    hostname VARCHAR(255) DEFAULT '',
                    display_name VARCHAR(255) DEFAULT NULL,
                    os_info VARCHAR(255) DEFAULT '',
                    ip VARCHAR(50) DEFAULT '',
                    mac_address VARCHAR(50) DEFAULT '',
                    screen_width INT DEFAULT 1920,
                    screen_height INT DEFAULT 1080,
                    group_name VARCHAR(100) DEFAULT 'default',
                    is_online BOOLEAN DEFAULT FALSE,
                    last_seen DATETIME,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE KEY uq_agent_owner (agent_id, owner_id),
                    FOREIGN KEY (owner_id) REFERENCES users(id) ON DELETE CASCADE
                )
            """)

            # agent_groups 테이블
            cur.execute("""
                CREATE TABLE IF NOT EXISTS agent_groups (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    name VARCHAR(100) NOT NULL,
                    description TEXT,
                    owner_id INT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE KEY uq_group_owner (name, owner_id),
                    FOREIGN KEY (owner_id) REFERENCES users(id) ON DELETE CASCADE
                )
            """)

            # admin 계정 초기화
            cur.execute("SELECT id, password FROM users WHERE username = 'admin'")
            admin = cur.fetchone()
            if not admin:
                hashed = hash_password("admin")
                cur.execute(
                    "INSERT INTO users (username, password, role, display_name) VALUES (%s, %s, 'admin', '관리자')",
                    ("admin", hashed)
                )
                print("[Init] admin 계정 생성 (초기 비밀번호: admin)")
            elif admin and not admin["password"].startswith("$2b$"):
                hashed = hash_password("admin")
                cur.execute("UPDATE users SET password = %s WHERE id = %s", (hashed, admin["id"]))
                print("[Init] admin 비밀번호 bcrypt 해싱 완료")

    print("[Init] 데이터베이스 초기화 완료")


# ===========================================================
# Auth
# ===========================================================
@app.post("/api/auth/login", response_model=LoginResponse)
def login(req: LoginRequest):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, username, password, role, display_name, is_active FROM users WHERE username = %s",
                (req.username,),
            )
            user = cur.fetchone()

    if not user:
        raise HTTPException(status_code=401, detail="사용자를 찾을 수 없습니다")
    if not user["is_active"]:
        raise HTTPException(status_code=401, detail="비활성화된 계정입니다")
    if not verify_password(req.password, user["password"]):
        raise HTTPException(status_code=401, detail="비밀번호가 올바르지 않습니다")

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET last_login = %s WHERE id = %s",
                (datetime.now(timezone.utc), user["id"]),
            )

    token = create_token(user["id"], user["username"], user["role"])
    return LoginResponse(
        token=token,
        user=UserInfo(
            id=user["id"],
            username=user["username"],
            role=user["role"],
            display_name=user["display_name"],
        ),
    )


@app.get("/api/auth/me", response_model=UserInfo)
def get_me(user: dict = Depends(get_current_user)):
    return UserInfo(
        id=user["id"],
        username=user["username"],
        role=user["role"],
        display_name=user["display_name"],
    )


# ===========================================================
# Agent 등록/관리 (에이전트 → 서버)
# ===========================================================
@app.post("/api/agents/register", response_model=AgentResponse)
def register_agent(req: AgentRegister, user: dict = Depends(get_current_user)):
    """에이전트가 서버에 자신을 등록 (로그인한 사용자의 소유로)"""
    with get_db() as conn:
        with conn.cursor() as cur:
            # 이미 등록된 에이전트인지 확인
            cur.execute(
                "SELECT id FROM agents WHERE agent_id = %s AND owner_id = %s",
                (req.agent_id, user["id"]),
            )
            existing = cur.fetchone()

            now = datetime.now(timezone.utc)

            if existing:
                # 기존 에이전트 업데이트
                cur.execute("""
                    UPDATE agents SET
                        hostname = %s, os_info = %s, ip = %s,
                        mac_address = %s, screen_width = %s, screen_height = %s,
                        is_online = TRUE, last_seen = %s
                    WHERE id = %s
                """, (
                    req.hostname, req.os_info, req.ip,
                    req.mac_address, req.screen_width, req.screen_height,
                    now, existing["id"],
                ))
                agent_id_db = existing["id"]
            else:
                # 신규 등록
                cur.execute("""
                    INSERT INTO agents
                        (agent_id, owner_id, hostname, os_info, ip,
                         mac_address, screen_width, screen_height,
                         is_online, last_seen)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, TRUE, %s)
                """, (
                    req.agent_id, user["id"],
                    req.hostname, req.os_info, req.ip,
                    req.mac_address, req.screen_width, req.screen_height,
                    now,
                ))
                agent_id_db = cur.lastrowid

            # 등록된 에이전트 반환
            cur.execute("""
                SELECT a.*, u.username as owner_username
                FROM agents a JOIN users u ON a.owner_id = u.id
                WHERE a.id = %s
            """, (agent_id_db,))
            agent = cur.fetchone()

    return _agent_to_response(agent)


@app.post("/api/agents/heartbeat")
def agent_heartbeat(req: AgentHeartbeat, user: dict = Depends(get_current_user)):
    """에이전트 하트비트 (주기적 상태 보고)"""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE agents SET
                    is_online = TRUE, last_seen = %s, ip = %s,
                    screen_width = %s, screen_height = %s
                WHERE agent_id = %s AND owner_id = %s
            """, (
                datetime.now(timezone.utc), req.ip,
                req.screen_width, req.screen_height,
                req.agent_id, user["id"],
            ))
    return {"status": "ok"}


@app.post("/api/agents/offline")
def agent_offline(req: AgentHeartbeat, user: dict = Depends(get_current_user)):
    """에이전트 오프라인 보고 (정상 종료 시)"""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE agents SET is_online = FALSE WHERE agent_id = %s AND owner_id = %s",
                (req.agent_id, user["id"]),
            )
    return {"status": "ok"}


# ===========================================================
# Agent 조회 (매니저 → 서버)
# ===========================================================
@app.get("/api/agents", response_model=list[AgentResponse])
def get_my_agents(user: dict = Depends(get_current_user)):
    """로그인한 사용자 소유의 에이전트 목록"""
    with get_db() as conn:
        with conn.cursor() as cur:
            if user["role"] == "admin":
                # 관리자: 전체 에이전트
                cur.execute("""
                    SELECT a.*, u.username as owner_username
                    FROM agents a JOIN users u ON a.owner_id = u.id
                    ORDER BY a.group_name, a.hostname
                """)
            else:
                # 일반 사용자: 자기 소유만
                cur.execute("""
                    SELECT a.*, u.username as owner_username
                    FROM agents a JOIN users u ON a.owner_id = u.id
                    WHERE a.owner_id = %s
                    ORDER BY a.group_name, a.hostname
                """, (user["id"],))
            agents = cur.fetchall()

    return [_agent_to_response(a) for a in agents]


@app.get("/api/agents/{agent_db_id}", response_model=AgentResponse)
def get_agent(agent_db_id: int, user: dict = Depends(get_current_user)):
    """특정 에이전트 조회"""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT a.*, u.username as owner_username
                FROM agents a JOIN users u ON a.owner_id = u.id
                WHERE a.id = %s
            """, (agent_db_id,))
            agent = cur.fetchone()

    if not agent:
        raise HTTPException(status_code=404, detail="에이전트를 찾을 수 없습니다")
    if user["role"] != "admin" and agent["owner_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="접근 권한이 없습니다")

    return _agent_to_response(agent)


@app.delete("/api/agents/{agent_db_id}")
def delete_agent(agent_db_id: int, user: dict = Depends(get_current_user)):
    """에이전트 삭제"""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT owner_id FROM agents WHERE id = %s", (agent_db_id,))
            agent = cur.fetchone()
            if not agent:
                raise HTTPException(status_code=404)
            if user["role"] != "admin" and agent["owner_id"] != user["id"]:
                raise HTTPException(status_code=403)
            cur.execute("DELETE FROM agents WHERE id = %s", (agent_db_id,))
    return {"status": "deleted"}


@app.put("/api/agents/{agent_db_id}/group")
def move_agent_group(agent_db_id: int, group_name: str = Query(...),
                     user: dict = Depends(get_current_user)):
    """에이전트 그룹 이동"""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT owner_id FROM agents WHERE id = %s", (agent_db_id,))
            agent = cur.fetchone()
            if not agent:
                raise HTTPException(status_code=404)
            if user["role"] != "admin" and agent["owner_id"] != user["id"]:
                raise HTTPException(status_code=403)
            cur.execute(
                "UPDATE agents SET group_name = %s WHERE id = %s",
                (group_name, agent_db_id),
            )
    return {"status": "ok"}


@app.put("/api/agents/{agent_db_id}/name")
def rename_agent(agent_db_id: int, display_name: str = Query(...),
                 user: dict = Depends(get_current_user)):
    """에이전트 표시 이름 변경"""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT owner_id FROM agents WHERE id = %s", (agent_db_id,))
            agent = cur.fetchone()
            if not agent:
                raise HTTPException(status_code=404)
            if user["role"] != "admin" and agent["owner_id"] != user["id"]:
                raise HTTPException(status_code=403)
            cur.execute(
                "UPDATE agents SET display_name = %s WHERE id = %s",
                (display_name, agent_db_id),
            )
    return {"status": "ok"}


# ===========================================================
# User 관리 (Admin)
# ===========================================================
@app.post("/api/admin/users", response_model=UserResponse)
def create_user(req: UserCreate, admin: dict = Depends(require_admin)):
    with get_db() as conn:
        with conn.cursor() as cur:
            hashed = hash_password(req.password)
            cur.execute("""
                INSERT INTO users (username, password, role, display_name)
                VALUES (%s, %s, %s, %s)
            """, (req.username, hashed, req.role, req.display_name))
            user_id = cur.lastrowid
            cur.execute("SELECT * FROM users WHERE id = %s", (user_id,))
            user = cur.fetchone()
    return _user_to_response(user)


@app.get("/api/admin/users", response_model=list[UserResponse])
def list_users(admin: dict = Depends(require_admin)):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM users ORDER BY id")
            users = cur.fetchall()
    return [_user_to_response(u) for u in users]


@app.put("/api/admin/users/{user_id}", response_model=UserResponse)
def update_user(user_id: int, req: UserUpdate, admin: dict = Depends(require_admin)):
    with get_db() as conn:
        with conn.cursor() as cur:
            updates = {}
            if req.display_name is not None:
                updates["display_name"] = req.display_name
            if req.role is not None:
                updates["role"] = req.role
            if req.is_active is not None:
                updates["is_active"] = req.is_active
            if req.password is not None:
                updates["password"] = hash_password(req.password)

            if updates:
                set_clause = ", ".join(f"{k} = %s" for k in updates)
                values = list(updates.values()) + [user_id]
                cur.execute(f"UPDATE users SET {set_clause} WHERE id = %s", values)

            cur.execute("SELECT * FROM users WHERE id = %s", (user_id,))
            user = cur.fetchone()
    if not user:
        raise HTTPException(status_code=404)
    return _user_to_response(user)


@app.delete("/api/admin/users/{user_id}")
def delete_user(user_id: int, admin: dict = Depends(require_admin)):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM users WHERE id = %s AND username != 'admin'", (user_id,))
    return {"status": "deleted"}


# ===========================================================
# Helpers
# ===========================================================
def _agent_to_response(agent: dict) -> AgentResponse:
    return AgentResponse(
        id=agent["id"],
        agent_id=agent["agent_id"],
        hostname=agent["hostname"],
        os_info=agent.get("os_info", ""),
        ip=agent.get("ip", ""),
        mac_address=agent.get("mac_address", ""),
        screen_width=agent.get("screen_width", 1920),
        screen_height=agent.get("screen_height", 1080),
        group_name=agent.get("group_name", "default"),
        display_name=agent.get("display_name"),
        is_online=bool(agent.get("is_online", False)),
        owner_id=agent["owner_id"],
        owner_username=agent.get("owner_username", ""),
        last_seen=str(agent["last_seen"]) if agent.get("last_seen") else None,
    )


def _user_to_response(user: dict) -> UserResponse:
    return UserResponse(
        id=user["id"],
        username=user["username"],
        role=user["role"],
        display_name=user.get("display_name"),
        is_active=bool(user.get("is_active", True)),
        created_at=str(user["created_at"]) if user.get("created_at") else None,
        last_login=str(user["last_login"]) if user.get("last_login") else None,
    )


# ===========================================================
# 실행
# ===========================================================
if __name__ == "__main__":
    import uvicorn
    from config import API_HOST, API_PORT
    uvicorn.run(app, host=API_HOST, port=API_PORT)
