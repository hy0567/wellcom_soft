"""
WellcomSOFT API 서버
FastAPI + MySQL + JWT 인증 + WebSocket 릴레이

핵심 흐름:
1. 에이전트(대상PC)가 서버에 로그인 → JWT 토큰 획득
2. 에이전트가 /api/agents/register로 자신을 등록 (owner_id = 로그인 사용자)
3. 매니저(관리PC)가 같은 계정으로 로그인 → /ws/manager?token=JWT 로 WS 접속
4. 에이전트가 /ws/agent?token=JWT 로 WS 접속
5. 서버가 같은 owner_id의 매니저↔에이전트 간 메시지를 양방향 릴레이
6. 포트포워딩 불필요 — 매니저/에이전트 모두 서버에 접속
"""
import os
import json
import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, Optional

from fastapi import FastAPI, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from starlette.websockets import WebSocketState

from auth import (
    hash_password, verify_password, create_token,
    get_current_user, require_admin, decode_token,
)
from database import get_db
from models import (
    LoginRequest, LoginResponse, UserInfo,
    UserCreate, UserUpdate, UserResponse,
    AgentRegister, AgentHeartbeat, AgentResponse,
    GroupCreate, GroupResponse,
    ManagerRegister, ManagerHeartbeat, ManagerResponse,
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
)
logger = logging.getLogger("ws_relay")

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

            # managers 테이블 (매니저 = 관리 PC)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS managers (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    owner_id INT NOT NULL,
                    ip VARCHAR(50) NOT NULL,
                    ws_port INT DEFAULT 4797,
                    is_online BOOLEAN DEFAULT FALSE,
                    last_seen DATETIME,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE KEY uq_manager_owner (owner_id),
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
# Manager 등록/조회 (매니저 IP를 에이전트가 알아가는 용도)
# ===========================================================
@app.post("/api/manager/register", response_model=ManagerResponse)
def register_manager(req: ManagerRegister, user: dict = Depends(get_current_user)):
    """매니저(관리PC)가 자신의 IP를 등록. 에이전트가 이 IP로 WS 연결."""
    with get_db() as conn:
        with conn.cursor() as cur:
            now = datetime.now(timezone.utc)
            cur.execute(
                "SELECT id FROM managers WHERE owner_id = %s",
                (user["id"],),
            )
            existing = cur.fetchone()

            if existing:
                cur.execute("""
                    UPDATE managers SET ip = %s, ws_port = %s,
                        is_online = TRUE, last_seen = %s
                    WHERE id = %s
                """, (req.ip, req.ws_port, now, existing["id"]))
                mgr_id = existing["id"]
            else:
                cur.execute("""
                    INSERT INTO managers (owner_id, ip, ws_port, is_online, last_seen)
                    VALUES (%s, %s, %s, TRUE, %s)
                """, (user["id"], req.ip, req.ws_port, now))
                mgr_id = cur.lastrowid

            cur.execute("SELECT * FROM managers WHERE id = %s", (mgr_id,))
            mgr = cur.fetchone()

    return _manager_to_response(mgr)


@app.post("/api/manager/heartbeat")
def manager_heartbeat(req: ManagerHeartbeat, user: dict = Depends(get_current_user)):
    """매니저 하트비트 (주기적 상태 보고)"""
    with get_db() as conn:
        with conn.cursor() as cur:
            now = datetime.now(timezone.utc)
            updates = ["is_online = TRUE", "last_seen = %s"]
            params = [now]
            if req.ip:
                updates.append("ip = %s")
                params.append(req.ip)
            params.append(user["id"])
            cur.execute(
                f"UPDATE managers SET {', '.join(updates)} WHERE owner_id = %s",
                params,
            )
    return {"status": "ok"}


@app.get("/api/manager", response_model=ManagerResponse)
def get_manager(user: dict = Depends(get_current_user)):
    """같은 계정의 매니저 IP 조회 (에이전트가 매니저에 WS 연결하기 위해 사용)"""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM managers WHERE owner_id = %s AND is_online = TRUE",
                (user["id"],),
            )
            mgr = cur.fetchone()

    if not mgr:
        raise HTTPException(status_code=404, detail="온라인 매니저를 찾을 수 없습니다")

    return _manager_to_response(mgr)


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


def _manager_to_response(mgr: dict) -> ManagerResponse:
    return ManagerResponse(
        id=mgr["id"],
        owner_id=mgr["owner_id"],
        ip=mgr["ip"],
        ws_port=mgr.get("ws_port", 4797),
        is_online=bool(mgr.get("is_online", False)),
        last_seen=str(mgr["last_seen"]) if mgr.get("last_seen") else None,
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
# WebSocket 릴레이 (매니저 ↔ 서버 ↔ 에이전트)
# ===========================================================

# 인메모리 릴레이 상태
_ws_managers: Dict[int, WebSocket] = {}                   # owner_id → 매니저 WS
_ws_agents: Dict[int, Dict[str, WebSocket]] = {}          # owner_id → {agent_id: WS}
_agent_owner_map: Dict[str, int] = {}                     # "owner_id:agent_id" → owner_id (역매핑)

# agent_id를 32바이트로 패딩/언패딩
AGENT_ID_LEN = 32


def _pad_agent_id(agent_id: str) -> bytes:
    """agent_id를 32바이트로 패딩"""
    return agent_id.encode('utf-8')[:AGENT_ID_LEN].ljust(AGENT_ID_LEN, b'\x00')


def _unpad_agent_id(data: bytes) -> str:
    """32바이트에서 agent_id 추출"""
    return data[:AGENT_ID_LEN].rstrip(b'\x00').decode('utf-8', errors='replace')


def _verify_ws_token(token: str) -> dict:
    """WebSocket용 JWT 토큰 검증 (query param)"""
    try:
        payload = decode_token(token)
        owner_id = payload.get("sub")
        username = payload.get("username", "")
        if not owner_id:
            return {}
        return {"id": owner_id, "username": username, "role": payload.get("role", "user")}
    except Exception:
        return {}


@app.websocket("/ws/manager")
async def ws_manager_endpoint(ws: WebSocket, token: str = Query(...)):
    """매니저 WS 접속 — JWT 인증 후 메시지 릴레이

    매니저가 보내는 메시지:
      - JSON: {"type": "...", "target_agent": "DESKTOP-ABC", ...}
        → target_agent 추출 → 해당 에이전트에 target_agent 제거 후 전달
      - Binary: agent_id(32바이트) + 원본 데이터
        → agent_id 추출 → 해당 에이전트에 원본 데이터 전달
    """
    user = _verify_ws_token(token)
    if not user:
        await ws.close(code=4001, reason="Invalid token")
        return

    owner_id = user["id"]
    await ws.accept()

    # 기존 매니저 연결이 있으면 교체
    old_ws = _ws_managers.get(owner_id)
    if old_ws:
        try:
            await old_ws.close(code=4002, reason="Replaced by new connection")
        except Exception:
            pass
        # 이전 연결 핸들러 정리 대기
        await asyncio.sleep(0.1)

    _ws_managers[owner_id] = ws
    logger.info(f"[WS Relay] 매니저 접속: owner_id={owner_id} ({user['username']})")

    # 이미 접속 중인 에이전트들의 auth 메시지를 매니저에 전달
    if owner_id in _ws_agents:
        for agent_id, agent_ws in _ws_agents[owner_id].items():
            try:
                await ws.send_text(json.dumps({
                    "type": "agent_connected",
                    "source_agent": agent_id,
                }))
            except Exception:
                pass

    try:
        while True:
            message = await ws.receive()
            msg_type = message.get("type")

            if msg_type == "websocket.receive":
                if "text" in message:
                    # JSON 메시지 — target_agent로 라우팅
                    raw = message["text"]
                    try:
                        data = json.loads(raw)
                    except json.JSONDecodeError:
                        continue

                    target_agent = data.pop("target_agent", None)
                    msg_cmd = data.get("type", "?")
                    if not target_agent:
                        logger.debug(f"[Relay M→A] target_agent 없음: type={msg_cmd}")
                        continue

                    # 해당 에이전트에 전달
                    agent_ws = _ws_agents.get(owner_id, {}).get(target_agent)
                    if agent_ws:
                        try:
                            await agent_ws.send_text(json.dumps(data))
                            if msg_cmd not in ('request_thumbnail', 'start_thumbnail_push', 'ping'):
                                logger.info(f"[Relay M→A] type={msg_cmd} → {target_agent}")
                        except Exception as e:
                            logger.warning(f"[Relay M→A] 전달 실패: {e}")
                    else:
                        logger.warning(f"[Relay M→A] 에이전트 없음: {target_agent} (등록: {list(_ws_agents.get(owner_id, {}).keys())})")

                elif "bytes" in message:
                    # 바이너리 메시지 — 앞 32바이트 = agent_id
                    raw_bytes = message["bytes"]
                    if len(raw_bytes) <= AGENT_ID_LEN:
                        continue

                    target_agent = _unpad_agent_id(raw_bytes[:AGENT_ID_LEN])
                    payload = raw_bytes[AGENT_ID_LEN:]

                    agent_ws = _ws_agents.get(owner_id, {}).get(target_agent)
                    if agent_ws:
                        try:
                            await agent_ws.send_bytes(payload)
                            logger.info(f"[Relay M→A] binary ({len(payload)}B) → {target_agent}")
                        except Exception as e:
                            logger.warning(f"[Relay M→A] 바이너리 전달 실패: {e}")
                    else:
                        logger.warning(f"[Relay M→A] 바이너리 에이전트 없음: {target_agent}")

            elif msg_type == "websocket.disconnect":
                break

    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.warning(f"[WS Relay] 매니저 오류 (owner_id={owner_id}): {e}")
    finally:
        if _ws_managers.get(owner_id) is ws:
            del _ws_managers[owner_id]
        logger.info(f"[WS Relay] 매니저 해제: owner_id={owner_id}")


@app.websocket("/ws/agent")
async def ws_agent_endpoint(ws: WebSocket, token: str = Query(...)):
    """에이전트 WS 접속 — JWT 인증 후 메시지 릴레이

    에이전트가 보내는 메시지:
      - 첫 메시지: {"type": "auth", "agent_id": "...", ...}
        → agent_id 추출/저장, 매니저에 그대로 전달
      - JSON: {"type": "clipboard", ...}
        → source_agent 추가 후 매니저에 전달
      - Binary: 0x01/0x02 + JPEG
        → agent_id(32바이트) 프리픽스 붙여서 매니저에 전달
    """
    user = _verify_ws_token(token)
    if not user:
        await ws.close(code=4001, reason="Invalid token")
        return

    owner_id = user["id"]
    await ws.accept()

    agent_id = None

    try:
        # 첫 메시지: auth 핸드셰이크
        raw = await asyncio.wait_for(ws.receive_text(), timeout=10)
        auth_msg = json.loads(raw)

        if auth_msg.get("type") != "auth":
            await ws.close(code=4003, reason="Expected auth message")
            return

        agent_id = auth_msg.get("agent_id", "")
        if not agent_id:
            await ws.close(code=4003, reason="Missing agent_id")
            return

        # 에이전트 등록
        if owner_id not in _ws_agents:
            _ws_agents[owner_id] = {}

        # 기존 같은 agent_id 연결이 있으면 교체
        old_ws = _ws_agents[owner_id].get(agent_id)
        if old_ws:
            try:
                await old_ws.close(code=4002, reason="Replaced")
            except Exception:
                pass

        _ws_agents[owner_id][agent_id] = ws
        logger.info(f"[WS Relay] 에이전트 접속: {agent_id} (owner_id={owner_id})")

        # 에이전트에 auth_ok 응답
        await ws.send_text(json.dumps({"type": "auth_ok"}))
        logger.info(f"[WS Relay] auth_ok 전송 완료: {agent_id}")

        # 매니저에 auth 메시지 전달 (agent_connected 트리거)
        mgr_ws = _ws_managers.get(owner_id)
        if mgr_ws:
            auth_msg["source_agent"] = agent_id
            try:
                await mgr_ws.send_text(json.dumps(auth_msg))
            except Exception:
                pass

        # 메시지 릴레이 루프
        agent_msg_count = 0
        while True:
            message = await ws.receive()
            msg_type = message.get("type")

            if msg_type == "websocket.receive":
                agent_msg_count += 1
                mgr_ws = _ws_managers.get(owner_id)

                if "text" in message:
                    raw_text = message["text"]
                    try:
                        data = json.loads(raw_text)
                    except json.JSONDecodeError:
                        continue

                    msg_cmd = data.get("type", "?")

                    if not mgr_ws:
                        if agent_msg_count <= 3:
                            logger.warning(f"[Agent {agent_id}] 매니저 미접속 — 메시지 버림: type={msg_cmd}")
                        continue

                    data["source_agent"] = agent_id
                    try:
                        await mgr_ws.send_text(json.dumps(data))
                        if msg_cmd not in ('pong', 'thumbnail_error'):
                            logger.debug(f"[Relay A→M] type={msg_cmd} ← {agent_id}")
                    except Exception as e:
                        logger.warning(f"[Relay A→M] 전달 실패: {e}")

                elif "bytes" in message:
                    raw_bytes = message["bytes"]
                    if not raw_bytes:
                        continue

                    if not mgr_ws:
                        continue

                    prefixed = _pad_agent_id(agent_id) + raw_bytes
                    try:
                        await mgr_ws.send_bytes(prefixed)
                    except Exception as e:
                        logger.warning(f"[Relay A→M] 바이너리 전달 실패: {e}")

            elif msg_type == "websocket.disconnect":
                logger.info(f"[Agent {agent_id}] WS 종료 (총 {agent_msg_count}개 메시지 수신)")
                break

    except asyncio.TimeoutError:
        logger.warning(f"[WS Relay] 에이전트 auth 타임아웃")
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.warning(f"[WS Relay] 에이전트 오류 ({agent_id or 'unknown'}): {e}")
    finally:
        if agent_id and owner_id in _ws_agents:
            if _ws_agents[owner_id].get(agent_id) is ws:
                del _ws_agents[owner_id][agent_id]
                if not _ws_agents[owner_id]:
                    del _ws_agents[owner_id]

            # 매니저에 disconnect 알림
            mgr_ws = _ws_managers.get(owner_id)
            if mgr_ws:
                try:
                    await mgr_ws.send_text(json.dumps({
                        "type": "agent_disconnected",
                        "source_agent": agent_id,
                    }))
                except Exception:
                    pass

        logger.info(f"[WS Relay] 에이전트 해제: {agent_id or 'unknown'} (owner_id={owner_id})")


# ===========================================================
# 실행
# ===========================================================
if __name__ == "__main__":
    import uvicorn
    from config import API_HOST, API_PORT

    uvicorn.run(app, host=API_HOST, port=API_PORT)
