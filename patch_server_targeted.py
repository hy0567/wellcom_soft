#!/usr/bin/env python3
"""
WellcomSOFT 서버 정밀 패치 (타겟팅 방식)
- 서버 고유 코드(Manager 관련 등) 보존
- 필요한 부분만 수정
실행: sudo python3 ~/patch_server_targeted.py && sudo systemctl restart wellcomsoft-api
"""
import os, shutil, sys
from datetime import datetime

SERVER_DIR = "/opt/wellcomsoft-api"
BACKUP_DIR = f"/opt/wellcomsoft-api/backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


def backup_files():
    os.makedirs(BACKUP_DIR, exist_ok=True)
    for fname in ["models.py", "main.py"]:
        src = os.path.join(SERVER_DIR, fname)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(BACKUP_DIR, fname))
            print(f"  백업: {fname} → {BACKUP_DIR}")


# ─── models.py 패치 ───────────────────────────────────────────

MODELS_AGENT_REGISTER_OLD = '''class AgentRegister(BaseModel):
    agent_id: str           # hostname 또는 UUID
    hostname: str
    os_info: str = ""
    ip: str = ""
    mac_address: str = ""
    screen_width: int = 1920
    screen_height: int = 1080'''

MODELS_AGENT_REGISTER_NEW = '''class AgentRegister(BaseModel):
    agent_id: str           # hostname 또는 UUID
    hostname: str
    os_info: str = ""
    ip: str = ""
    ip_public: str = ""     # 공인IP (P2P용)
    ws_port: int = 21350    # 에이전트 WS 서버 포트 (P2P용)
    mac_address: str = ""
    screen_width: int = 1920
    screen_height: int = 1080
    agent_version: str = ""
    cpu_model: str = ""
    cpu_cores: int = 0
    ram_gb: float = 0.0
    motherboard: str = ""
    gpu_model: str = ""'''

MODELS_AGENT_HEARTBEAT_OLD = '''class AgentHeartbeat(BaseModel):
    agent_id: str
    ip: str = ""
    screen_width: int = 1920
    screen_height: int = 1080'''

MODELS_AGENT_HEARTBEAT_NEW = '''class AgentHeartbeat(BaseModel):
    agent_id: str
    ip: str = ""
    ip_public: str = ""
    ws_port: int = 21350
    screen_width: int = 1920
    screen_height: int = 1080
    agent_version: str = ""'''

MODELS_AGENT_RESPONSE_OLD = '''class AgentResponse(BaseModel):
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
    last_seen: Optional[str] = None'''

MODELS_AGENT_RESPONSE_NEW = '''class AgentResponse(BaseModel):
    id: int
    agent_id: str
    hostname: str
    os_info: str = ""
    ip: str = ""
    ip_public: str = ""
    ws_port: int = 21350
    mac_address: str = ""
    screen_width: int = 1920
    screen_height: int = 1080
    group_name: str = "default"
    display_name: Optional[str] = None
    is_online: bool = False
    owner_id: int = 0
    owner_username: str = ""
    last_seen: Optional[str] = None
    agent_version: str = ""
    cpu_model: str = ""
    cpu_cores: int = 0
    ram_gb: float = 0.0
    motherboard: str = ""
    gpu_model: str = ""'''


# ─── main.py 패치 ────────────────────────────────────────────

# 1) imports 추가
MAIN_IMPORTS_OLD = '''import os
import json
import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, Optional

from fastapi import FastAPI, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect'''

MAIN_IMPORTS_NEW = '''import os
import json
import asyncio
import logging
import secrets
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

from fastapi import FastAPI, Depends, HTTPException, Query, Request, WebSocket, WebSocketDisconnect'''

# 2) 로그인 rate limit 상수 (startup_init 앞에 삽입)
MAIN_RATE_LIMIT_INSERT_AFTER = 'logger = logging.getLogger("ws_relay")'
MAIN_RATE_LIMIT_CODE = '''
logger = logging.getLogger("ws_relay")

# 로그인 속도 제한
_login_attempts: dict = defaultdict(list)
RATE_LIMIT_WINDOW = 60
RATE_LIMIT_MAX = 10

def _check_rate_limit(ip: str):
    """IP당 60초 이내 10회 초과 시 429"""
    now = time.time()
    _login_attempts[ip] = [t for t in _login_attempts[ip] if now - t < RATE_LIMIT_WINDOW]
    if len(_login_attempts[ip]) >= RATE_LIMIT_MAX:
        raise HTTPException(
            status_code=429,
            detail=f"로그인 시도 횟수 초과. {RATE_LIMIT_WINDOW}초 후 다시 시도하세요.",
        )
    _login_attempts[ip].append(now)'''

# 3) startup_init → async
MAIN_STARTUP_OLD = '@app.on_event("startup")\ndef startup_init():'
MAIN_STARTUP_NEW = '@app.on_event("startup")\nasync def startup_init():'

# 4) admin 비밀번호 랜덤 생성
MAIN_ADMIN_PW_OLD = '''            if not admin:
                hashed = hash_password("admin")
                cur.execute(
                    "INSERT INTO users (username, password, role, display_name) VALUES (%s, %s, 'admin', '관리자')",
                    ("admin", hashed)
                )
                print("[Init] admin 계정 생성 (초기 비밀번호: admin)")'''

MAIN_ADMIN_PW_NEW = '''            if not admin:
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
                print("=" * 60)'''

# 5) DB 컬럼 추가 (startup_init 내 컬럼 마이그레이션)
MAIN_DB_COLS_INSERT_AFTER = '    print("[Init] 데이터베이스 초기화 완료")'
MAIN_DB_COLS_CODE = '''    print("[Init] 데이터베이스 초기화 완료")

    # 백그라운드: 오프라인 에이전트 자동 정리
    asyncio.create_task(_cleanup_stale_agents())'''

# 6) _cleanup_stale_agents 함수 (login 함수 앞에 삽입)
MAIN_CLEANUP_FUNC = '''

async def _cleanup_stale_agents():
    """하트비트 3분 초과 에이전트 자동 오프라인 처리"""
    while True:
        await asyncio.sleep(120)
        try:
            with get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        UPDATE agents SET is_online = FALSE
                        WHERE is_online = TRUE
                          AND last_seen < %s
                    """, (datetime.now(timezone.utc) - timedelta(minutes=3),))
                    if cur.rowcount:
                        logger.info(f"[Cleanup] 오프라인 처리: {cur.rowcount}개")
        except Exception as e:
            logger.warning(f"[Cleanup] 오류: {e}")


'''

# 7) login 함수에 rate limit 추가
MAIN_LOGIN_OLD = '''@app.post("/api/auth/login", response_model=LoginResponse)
def login(req: LoginRequest):'''

MAIN_LOGIN_NEW = '''@app.post("/api/auth/login", response_model=LoginResponse)
def login(req: LoginRequest, request: Request):
    _check_rate_limit(request.client.host if request.client else "unknown")'''

# 8) register_agent UPDATE SQL
MAIN_REGISTER_UPDATE_OLD = '''                cur.execute("""
                    UPDATE agents SET
                        hostname = %s, os_info = %s, ip = %s,
                        mac_address = %s, screen_width = %s, screen_height = %s,
                        is_online = TRUE, last_seen = %s
                    WHERE id = %s
                """, (
                    req.hostname, req.os_info, req.ip,
                    req.mac_address, req.screen_width, req.screen_height,
                    now, existing["id"],
                ))'''

MAIN_REGISTER_UPDATE_NEW = '''                cur.execute("""
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
                    getattr(req, 'ip_public', ''), getattr(req, 'ws_port', 21350),
                    req.mac_address, req.screen_width, req.screen_height,
                    getattr(req, 'agent_version', ''),
                    getattr(req, 'cpu_model', ''), getattr(req, 'cpu_cores', 0),
                    getattr(req, 'ram_gb', 0.0),
                    getattr(req, 'motherboard', ''), getattr(req, 'gpu_model', ''),
                    now, existing["id"],
                ))'''

# 9) register_agent INSERT SQL
MAIN_REGISTER_INSERT_OLD = '''                cur.execute("""
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
                ))'''

MAIN_REGISTER_INSERT_NEW = '''                cur.execute("""
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
                    getattr(req, 'ip_public', ''), getattr(req, 'ws_port', 21350),
                    req.mac_address, req.screen_width, req.screen_height,
                    getattr(req, 'agent_version', ''),
                    getattr(req, 'cpu_model', ''), getattr(req, 'cpu_cores', 0),
                    getattr(req, 'ram_gb', 0.0),
                    getattr(req, 'motherboard', ''), getattr(req, 'gpu_model', ''),
                    now,
                ))'''

# 10) heartbeat SQL
MAIN_HEARTBEAT_OLD = '''            cur.execute("""
                UPDATE agents SET
                    is_online = TRUE, last_seen = %s, ip = %s,
                    screen_width = %s, screen_height = %s
                WHERE agent_id = %s AND owner_id = %s
            """, (
                datetime.now(timezone.utc), req.ip,
                req.screen_width, req.screen_height,
                req.agent_id, user["id"],
            ))'''

MAIN_HEARTBEAT_NEW = '''            cur.execute("""
                UPDATE agents SET
                    is_online = TRUE, last_seen = %s, ip = %s,
                    ip_public = %s, ws_port = %s,
                    screen_width = %s, screen_height = %s,
                    agent_version = %s
                WHERE agent_id = %s AND owner_id = %s
            """, (
                datetime.now(timezone.utc), req.ip,
                getattr(req, 'ip_public', ''), getattr(req, 'ws_port', 21350),
                req.screen_width, req.screen_height,
                getattr(req, 'agent_version', ''),
                req.agent_id, user["id"],
            ))'''

# 11) _agent_to_response
MAIN_AGENT_RESP_OLD = '''def _agent_to_response(agent: dict) -> AgentResponse:
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
    )'''

MAIN_AGENT_RESP_NEW = '''def _agent_to_response(agent: dict) -> AgentResponse:
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
    )'''


def patch_models(content: str) -> str:
    patches = [
        (MODELS_AGENT_REGISTER_OLD, MODELS_AGENT_REGISTER_NEW, "AgentRegister"),
        (MODELS_AGENT_HEARTBEAT_OLD, MODELS_AGENT_HEARTBEAT_NEW, "AgentHeartbeat"),
        (MODELS_AGENT_RESPONSE_OLD, MODELS_AGENT_RESPONSE_NEW, "AgentResponse"),
    ]
    for old, new, name in patches:
        if old in content:
            content = content.replace(old, new, 1)
            print(f"  ✓ {name} 업데이트")
        else:
            print(f"  ⚠ {name} 패턴 없음 (이미 패치됨?)")
    return content


def patch_main(content: str) -> str:
    patches = [
        (MAIN_IMPORTS_OLD,          MAIN_IMPORTS_NEW,          "imports"),
        (MAIN_STARTUP_OLD,          MAIN_STARTUP_NEW,          "startup_init → async"),
        (MAIN_ADMIN_PW_OLD,         MAIN_ADMIN_PW_NEW,         "admin 랜덤 비밀번호"),
        (MAIN_LOGIN_OLD,            MAIN_LOGIN_NEW,            "login rate limit"),
        (MAIN_REGISTER_UPDATE_OLD,  MAIN_REGISTER_UPDATE_NEW,  "register UPDATE SQL"),
        (MAIN_REGISTER_INSERT_OLD,  MAIN_REGISTER_INSERT_NEW,  "register INSERT SQL"),
        (MAIN_HEARTBEAT_OLD,        MAIN_HEARTBEAT_NEW,        "heartbeat SQL"),
        (MAIN_AGENT_RESP_OLD,       MAIN_AGENT_RESP_NEW,       "_agent_to_response"),
    ]
    for old, new, name in patches:
        if old in content:
            content = content.replace(old, new, 1)
            print(f"  ✓ {name} 업데이트")
        else:
            print(f"  ⚠ {name} 패턴 없음 (이미 패치됨?)")

    # rate limit 상수 + 함수 (logger 선언 바로 뒤에 삽입)
    old_logger = 'logger = logging.getLogger("ws_relay")'
    if old_logger in content and '_login_attempts' not in content:
        content = content.replace(
            old_logger,
            MAIN_RATE_LIMIT_CODE,
            1
        )
        print("  ✓ rate limit 상수/함수 삽입")

    # _cleanup_stale_agents 함수 삽입 (login 함수 앞)
    login_marker = '@app.post("/api/auth/login", response_model=LoginResponse)'
    if '_cleanup_stale_agents' not in content and login_marker in content:
        content = content.replace(
            login_marker,
            MAIN_CLEANUP_FUNC + login_marker,
            1,
        )
        print("  ✓ _cleanup_stale_agents 함수 삽입")

    # stale cleanup task 등록 (startup_init 끝에)
    done_marker = '    print("[Init] 데이터베이스 초기화 완료")'
    if done_marker in content and 'create_task(_cleanup' not in content:
        content = content.replace(
            done_marker,
            MAIN_DB_COLS_CODE,
            1,
        )
        print("  ✓ stale cleanup task 등록")

    # DB 컬럼 마이그레이션 삽입
    # agent_groups CREATE TABLE 이후 위치에 삽입
    groups_table_end = "            # admin 계정 초기화"
    if "ip_public" not in content and groups_table_end in content:
        migration_block = """            # P2P용 컬럼 추가 (마이그레이션)
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
                    print(f"[Init] agents 컬럼 추가: {col_name}")
                except Exception:
                    pass

"""
        content = content.replace(
            groups_table_end,
            migration_block + groups_table_end,
            1,
        )
        print("  ✓ DB 컬럼 마이그레이션 삽입")

    return content


def main():
    print("=" * 60)
    print("WellcomSOFT 서버 정밀 패치 v3.1.0")
    print("=" * 60)

    if not os.path.exists(SERVER_DIR):
        print(f"오류: {SERVER_DIR} 없음")
        sys.exit(1)

    print("\n[1] 백업...")
    backup_files()

    # models.py 패치
    print("\n[2] models.py 패치...")
    models_path = os.path.join(SERVER_DIR, "models.py")
    with open(models_path, encoding="utf-8") as f:
        models_content = f.read()
    models_patched = patch_models(models_content)
    with open(models_path, "w", encoding="utf-8") as f:
        f.write(models_patched)

    # main.py 패치
    print("\n[3] main.py 패치...")
    main_path = os.path.join(SERVER_DIR, "main.py")
    with open(main_path, encoding="utf-8") as f:
        main_content = f.read()
    main_patched = patch_main(main_content)
    with open(main_path, "w", encoding="utf-8") as f:
        f.write(main_patched)

    # 문법 검증
    print("\n[4] 문법 검증...")
    import ast
    for fname, patched in [("models.py", models_patched), ("main.py", main_patched)]:
        try:
            ast.parse(patched)
            print(f"  ✓ {fname} 문법 OK")
        except SyntaxError as e:
            print(f"  ✗ {fname} 문법 오류! line {e.lineno}: {e.msg}")
            print(f"    ⚠ 백업에서 복원 중...")
            shutil.copy2(os.path.join(BACKUP_DIR, fname), os.path.join(SERVER_DIR, fname))
            print(f"    ✓ {fname} 복원 완료")
            sys.exit(1)

    print("\n" + "=" * 60)
    print("패치 완료! 서버를 재시작하세요:")
    print("  sudo systemctl restart wellcomsoft-api")
    print("=" * 60)


if __name__ == "__main__":
    main()
