"""
WellcomSOFT API 서버
FastAPI + MySQL + JWT 인증

핵심 흐름:
1. 에이전트(대상PC)가 서버에 로그인 → JWT 토큰 획득
2. 에이전트가 /api/agents/register로 자신을 등록 (owner_id = 로그인 사용자)
3. 에이전트가 /api/agents/heartbeat로 주기적으로 상태 보고
4. 매니저(관리PC)가 같은 계정으로 로그인 → /api/agents로 해당 사용자의 에이전트 목록 조회
5. 매니저가 에이전트의 IP를 알아내서 WebSocket 직접 연결
   OR 포트 개방 불가 시 → /ws/manager, /ws/agent 릴레이를 통해 서버 중계
"""
import asyncio
import json
import os
import secrets
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import FastAPI, Depends, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

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
)

# ===========================================================
# 서버 릴레이 상태 (메모리, 단일 프로세스)
# 에이전트가 아웃바운드로 연결 → 포트 개방 불필요 폴백
# ===========================================================
_relay_agents: dict = {}   # agent_id → WebSocket (에이전트 측)
_relay_agent_info: dict = {}  # agent_id → {"real_ip": str, "ws_port": int}
_relay_managers: set = set()  # 연결된 매니저 WebSocket 목록 (set: O(1) 추가/삭제)

# ===========================================================
# 로그인 속도 제한 (브루트포스 방지)
# ===========================================================
_login_attempts: dict = defaultdict(list)   # IP → [timestamp, ...]
RATE_LIMIT_WINDOW = 60      # 초
RATE_LIMIT_MAX = 10         # 창당 최대 시도

RELAY_AGENT_ID_LEN = 32


def _relay_pad(agent_id: str) -> bytes:
    return agent_id.encode("utf-8")[:RELAY_AGENT_ID_LEN].ljust(RELAY_AGENT_ID_LEN, b"\x00")


def _relay_unpad(data: bytes) -> str:
    return data[:RELAY_AGENT_ID_LEN].rstrip(b"\x00").decode("utf-8", errors="replace")

app = FastAPI(title="WellcomSOFT API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ===========================================================
# WebSocket 릴레이 (포트 21350 개방 불가 시 폴백)
# ===========================================================

@app.websocket("/ws/agent")
async def ws_agent_relay(websocket: WebSocket, token: str = Query(default="")):
    """에이전트 → 서버 아웃바운드 릴레이 연결 (포트 개방 불필요)"""
    await websocket.accept()

    # JWT 검증
    try:
        payload = decode_token(token)
    except Exception:
        await websocket.close(code=4001, reason="Unauthorized")
        return

    # 에이전트 hello 핸드셰이크
    agent_id = None
    try:
        raw = await asyncio.wait_for(websocket.receive_text(), timeout=10)
        init = json.loads(raw)
        if init.get("type") == "agent_hello":
            agent_id = str(init.get("agent_id", "")).strip()
    except Exception:
        pass

    if not agent_id:
        await websocket.close(code=4000, reason="No agent_id")
        return

    _relay_agents[agent_id] = websocket
    # 에이전트의 실제 접속 IP + WS 포트 저장 (NAT 뒤에서도 공인IP 알 수 있음)
    agent_real_ip = websocket.client.host if websocket.client else ''
    agent_ws_port = init.get("ws_port", 21350)
    _relay_agent_info[agent_id] = {"real_ip": agent_real_ip, "ws_port": agent_ws_port}
    await websocket.send_text(json.dumps({"type": "relay_ok"}))

    # DB에 에이전트의 실제 공인IP 업데이트 (ip_public이 비어있으면 채워줌)
    if agent_real_ip:
        try:
            owner_id = payload.get("user_id") or payload.get("sub")
            if owner_id:
                with get_db() as conn:
                    with conn.cursor() as cur:
                        cur.execute("""
                            UPDATE agents SET ip_public = %s
                            WHERE agent_id = %s AND owner_id = %s
                              AND (ip_public IS NULL OR ip_public = '')
                        """, (agent_real_ip, agent_id, owner_id))
                        if cur.rowcount:
                            print(f"[Relay] DB ip_public 업데이트: {agent_id} → {agent_real_ip}")
        except Exception as e:
            print(f"[Relay] DB ip_public 업데이트 실패: {e}")

    # 매니저들에게 에이전트 접속 알림 (real_ip + ws_port 포함 → P2P 직접 연결용)
    notify = json.dumps({
        "type": "agent_connected",
        "source_agent": agent_id,
        "real_ip": agent_real_ip,
        "ws_port": agent_ws_port,
    })
    for m_ws in list(_relay_managers):
        try:
            await m_ws.send_text(notify)
        except Exception:
            pass
    print(f"[Relay] 에이전트 접속: {agent_id} (IP: {agent_real_ip})")

    try:
        while True:
            data = await websocket.receive()
            if data.get("type") == "websocket.disconnect":
                break

            text = data.get("text")
            raw_bytes = data.get("bytes")

            if text:
                # JSON: source_agent 추가 후 모든 매니저에게 브로드캐스트
                try:
                    msg = json.loads(text)
                except Exception:
                    continue
                msg["source_agent"] = agent_id
                fwd = json.dumps(msg)
                for m_ws in list(_relay_managers):
                    try:
                        await m_ws.send_text(fwd)
                    except Exception:
                        pass

            elif raw_bytes:
                # 바이너리: 32B agent_id 접두어 추가 후 브로드캐스트
                fwd = _relay_pad(agent_id) + raw_bytes
                for m_ws in list(_relay_managers):
                    try:
                        await m_ws.send_bytes(fwd)
                    except Exception:
                        pass

    except (WebSocketDisconnect, Exception):
        pass
    finally:
        _relay_agents.pop(agent_id, None)
        _relay_agent_info.pop(agent_id, None)
        # 매니저들에게 에이전트 해제 알림
        notify = json.dumps({"type": "agent_disconnected", "source_agent": agent_id})
        for m_ws in list(_relay_managers):
            try:
                await m_ws.send_text(notify)
            except Exception:
                pass
        print(f"[Relay] 에이전트 해제: {agent_id}")


@app.websocket("/ws/manager")
async def ws_manager_relay(websocket: WebSocket, token: str = Query(default="")):
    """매니저 → 서버 릴레이 연결 (P2P 실패 시 폴백)"""
    await websocket.accept()

    # JWT 검증
    try:
        decode_token(token)
    except Exception:
        await websocket.close(code=4001, reason="Unauthorized")
        return

    _relay_managers.add(websocket)
    await websocket.send_text(json.dumps({"type": "relay_ok"}))

    # 현재 연결된 에이전트 목록 전달 (real_ip + ws_port 포함)
    for aid in list(_relay_agents.keys()):
        try:
            info = _relay_agent_info.get(aid, {})
            await websocket.send_text(json.dumps({
                "type": "agent_connected",
                "source_agent": aid,
                "real_ip": info.get("real_ip", ""),
                "ws_port": info.get("ws_port", 21350),
            }))
        except Exception:
            pass
    print(f"[Relay] 매니저 접속 (릴레이 에이전트: {len(_relay_agents)}개)")

    try:
        while True:
            data = await websocket.receive()
            if data.get("type") == "websocket.disconnect":
                break

            text = data.get("text")
            raw_bytes = data.get("bytes")

            if text:
                # JSON: target_agent 추출 → 해당 에이전트에 전달
                try:
                    msg = json.loads(text)
                except Exception:
                    continue
                target = msg.pop("target_agent", None)
                if target:
                    agent_ws = _relay_agents.get(target)
                    if agent_ws:
                        try:
                            await agent_ws.send_text(json.dumps(msg))
                        except Exception:
                            pass

            elif raw_bytes:
                # 바이너리: 앞 32B = target agent_id
                if len(raw_bytes) < RELAY_AGENT_ID_LEN + 1:
                    continue
                target = _relay_unpad(raw_bytes[:RELAY_AGENT_ID_LEN])
                payload = raw_bytes[RELAY_AGENT_ID_LEN:]
                agent_ws = _relay_agents.get(target)
                if agent_ws:
                    try:
                        await agent_ws.send_bytes(payload)
                    except Exception:
                        pass

    except (WebSocketDisconnect, Exception):
        pass
    finally:
        _relay_managers.discard(websocket)
        print("[Relay] 매니저 해제")


# ===========================================================
# 시작 시 테이블 초기화
# ===========================================================
@app.on_event("startup")
async def startup_init():
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

            # P2P용 컬럼 추가 (v3.0.0) + 업데이터용 컬럼 (v3.1.0) + 하드웨어 컬럼 (v3.0.7)
            for col_name, col_def in [
                ('ip_public', "VARCHAR(50) DEFAULT ''"),
                ('ws_port', "INT DEFAULT 21350"),
                ('agent_version', "VARCHAR(20) DEFAULT ''"),
                ('cpu_model', "VARCHAR(255) DEFAULT ''"),
                ('cpu_cores', "INT DEFAULT 0"),
                ('ram_gb', "FLOAT DEFAULT 0.0"),
                ('motherboard', "VARCHAR(255) DEFAULT ''"),
                ('gpu_model', "VARCHAR(255) DEFAULT ''"),
            ]:
                try:
                    cur.execute(f"ALTER TABLE agents ADD COLUMN {col_name} {col_def}")
                    print(f"[Init] agents 테이블에 {col_name} 컬럼 추가")
                except Exception:
                    pass  # 이미 존재

            # admin 계정 초기화
            cur.execute("SELECT id, password FROM users WHERE username = 'admin'")
            admin = cur.fetchone()
            if not admin:
                # 최초 생성: 무작위 비밀번호 생성
                random_pw = secrets.token_urlsafe(12)
                hashed = hash_password(random_pw)
                cur.execute(
                    "INSERT INTO users (username, password, role, display_name) VALUES (%s, %s, 'admin', '관리자')",
                    ("admin", hashed)
                )
                print("=" * 60)
                print(f"[Init] admin 계정 생성")
                print(f"[Init] ★ 초기 비밀번호: {random_pw}")
                print(f"[Init] ★ 로그인 후 반드시 비밀번호를 변경하세요!")
                print("=" * 60)
            elif admin and not admin["password"].startswith("$2b$"):
                hashed = hash_password("admin")
                cur.execute("UPDATE users SET password = %s WHERE id = %s", (hashed, admin["id"]))
                print("[Init] admin 비밀번호 bcrypt 해싱 완료")

    print("[Init] 데이터베이스 초기화 완료")

    # 백그라운드 태스크: 오래된 에이전트 오프라인 처리
    asyncio.create_task(_cleanup_stale_agents())


# ===========================================================
# Auth
# ===========================================================
async def _cleanup_stale_agents():
    """하트비트 미수신 에이전트 자동 오프라인 처리 (3분 이상 미응답)"""
    STALE_MINUTES = 3
    while True:
        await asyncio.sleep(120)   # 2분마다 실행
        try:
            with get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        UPDATE agents SET is_online = FALSE
                        WHERE is_online = TRUE
                          AND last_seen < %s
                    """, (datetime.now(timezone.utc) - timedelta(minutes=STALE_MINUTES),))
                    if cur.rowcount:
                        print(f"[Cleanup] 오프라인 처리: {cur.rowcount}개 에이전트 (마지막 하트비트 {STALE_MINUTES}분 초과)")
        except Exception as e:
            print(f"[Cleanup] 오류: {e}")


def _check_rate_limit(ip: str):
    """로그인 속도 제한 확인 (IP당 {RATE_LIMIT_MAX}회/{RATE_LIMIT_WINDOW}초)"""
    now = time.time()
    attempts = _login_attempts[ip]
    # 오래된 시도 제거
    _login_attempts[ip] = [t for t in attempts if now - t < RATE_LIMIT_WINDOW]
    if len(_login_attempts[ip]) >= RATE_LIMIT_MAX:
        raise HTTPException(
            status_code=429,
            detail=f"로그인 시도 횟수 초과 ({RATE_LIMIT_MAX}회/{RATE_LIMIT_WINDOW}초). 잠시 후 다시 시도하세요.",
        )
    _login_attempts[ip].append(now)


@app.post("/api/auth/login", response_model=LoginResponse)
def login(req: LoginRequest, request: Request):
    _check_rate_limit(request.client.host if request.client else "unknown")
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
                        ip_public = %s, ws_port = %s,
                        mac_address = %s, screen_width = %s, screen_height = %s,
                        agent_version = %s,
                        cpu_model = %s, cpu_cores = %s, ram_gb = %s,
                        motherboard = %s, gpu_model = %s,
                        is_online = TRUE, last_seen = %s
                    WHERE id = %s
                """, (
                    req.hostname, req.os_info, req.ip,
                    req.ip_public, req.ws_port,
                    req.mac_address, req.screen_width, req.screen_height,
                    req.agent_version,
                    req.cpu_model, req.cpu_cores, req.ram_gb,
                    req.motherboard, req.gpu_model,
                    now, existing["id"],
                ))
                agent_id_db = existing["id"]
            else:
                # 신규 등록
                cur.execute("""
                    INSERT INTO agents
                        (agent_id, owner_id, hostname, os_info, ip,
                         ip_public, ws_port,
                         mac_address, screen_width, screen_height,
                         agent_version,
                         cpu_model, cpu_cores, ram_gb, motherboard, gpu_model,
                         is_online, last_seen)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, TRUE, %s)
                """, (
                    req.agent_id, user["id"],
                    req.hostname, req.os_info, req.ip,
                    req.ip_public, req.ws_port,
                    req.mac_address, req.screen_width, req.screen_height,
                    req.agent_version,
                    req.cpu_model, req.cpu_cores, req.ram_gb,
                    req.motherboard, req.gpu_model,
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
                    ip_public = %s, ws_port = %s,
                    screen_width = %s, screen_height = %s,
                    agent_version = %s
                WHERE agent_id = %s AND owner_id = %s
            """, (
                datetime.now(timezone.utc), req.ip,
                req.ip_public, req.ws_port,
                req.screen_width, req.screen_height,
                req.agent_version,
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
        ip_public=agent.get("ip_public", ""),
        ws_port=agent.get("ws_port", 21350),
        mac_address=agent.get("mac_address", ""),
        screen_width=agent.get("screen_width", 1920),
        screen_height=agent.get("screen_height", 1080),
        group_name=agent.get("group_name", "default"),
        display_name=agent.get("display_name"),
        is_online=bool(agent.get("is_online", False)),
        owner_id=agent["owner_id"],
        owner_username=agent.get("owner_username", ""),
        last_seen=str(agent["last_seen"]) if agent.get("last_seen") else None,
        agent_version=agent.get("agent_version", ""),
        cpu_model=agent.get("cpu_model", ""),
        cpu_cores=agent.get("cpu_cores", 0),
        ram_gb=agent.get("ram_gb", 0.0),
        motherboard=agent.get("motherboard", ""),
        gpu_model=agent.get("gpu_model", ""),
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
