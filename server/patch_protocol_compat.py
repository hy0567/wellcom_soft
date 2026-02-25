#!/usr/bin/env python3
"""서버 프로토콜 호환성 패치

배포된 서버(/opt/wellcomsoft-api/main.py)가 agent_hello 프로토콜도 수용하도록 수정.
현재: type=="auth" 만 허용 → 수정 후: type in ("auth", "agent_hello") 허용

사용법:
  sudo python3 /tmp/patch_protocol_compat.py
"""
import re
import shutil
from pathlib import Path

TARGET = Path("/opt/wellcomsoft-api/main.py")


def patch():
    if not TARGET.exists():
        print(f"[ERROR] {TARGET} 파일 없음")
        return False

    code = TARGET.read_text(encoding="utf-8")

    # 이미 패치됨?
    if 'agent_hello' in code:
        print("[OK] 이미 패치됨 (agent_hello 포함)")
        return True

    # 백업
    bak = TARGET.with_suffix(".py.bak3")
    shutil.copy2(TARGET, bak)
    print(f"[BACKUP] {bak}")

    modified = False

    # 패치 1: auth_msg.get("type") != "auth" → not in ("auth", "agent_hello")
    old_pattern = r'''if auth_msg\.get\("type"\) != "auth":'''
    new_value = 'if auth_msg.get("type") not in ("auth", "agent_hello"):'
    if re.search(old_pattern, code):
        code = re.sub(old_pattern, new_value, code)
        modified = True
        print("[PATCH 1] auth 체크 → auth + agent_hello 양쪽 허용")

    # 패치 2: auth_ok 응답 직후 → agent_hello인 경우 relay_ok도 전송
    # 기존 서버는 auth_ok를 await ws.send_text(json.dumps({...})) 형태로 보냄
    # agent_hello 프로토콜 클라이언트를 위해 relay_ok도 추가 전송
    # 에이전트는 이미 auth_ok도 수용하도록 수정했으므로 서버는 기존 auth_ok만 보내도 됨
    # → 서버 패치는 패치1만으로 충분 (agent가 auth_ok를 수용)

    if modified:
        TARGET.write_text(code, encoding="utf-8")
        print("[DONE] 패치 완료 — sudo systemctl restart wellcomsoft-api")
    else:
        print("[WARN] 패치 대상 패턴을 찾지 못함")
        # 디버깅: 현재 auth 관련 코드 출력
        for i, line in enumerate(code.splitlines(), 1):
            if 'auth' in line.lower() and 'type' in line.lower():
                print(f"  L{i}: {line.rstrip()}")

    return modified


if __name__ == "__main__":
    patch()
