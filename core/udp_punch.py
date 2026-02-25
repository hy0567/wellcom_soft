"""UDP NAT 홀펀칭 — STUN 탐지 + 시그널링 + 동시 UDP 전송

LinkIO 방식: 포트포워딩 없이 NAT 뒤의 두 피어가 직접 UDP 통신 가능.

흐름:
1. 매니저: UDP 소켓 생성 → STUN 탐지 → udp_offer 전송 (릴레이 경유)
2. 에이전트: udp_offer 수신 → UDP 소켓 생성 → STUN 탐지 → udp_answer 응답
3. 양쪽 동시 UDP 전송 (3초간) → NAT 홀 뚫림
4. 수신 성공 → UdpChannel 반환
"""

import asyncio
import os
import socket
import struct
import time
import logging
from typing import Optional

from .stun_client import stun_discover
from .udp_channel import UdpChannel

logger = logging.getLogger(__name__)

# 홀펀칭 상수
PUNCH_MAGIC = b'\x57\x43\x50\x48'  # 'WCPH' - WellCom Punch Hello
PUNCH_ACK = b'\x57\x43\x50\x41'    # 'WCPA' - WellCom Punch Ack
PUNCH_DURATION = 4.0     # 홀펀칭 시도 시간 (초)
PUNCH_INTERVAL = 0.05    # 패킷 전송 간격 (50ms)
PUNCH_TIMEOUT = 5.0      # 전체 타임아웃


def _create_udp_socket() -> socket.socket:
    """NAT 홀펀칭용 UDP 소켓 생성"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(('0.0.0.0', 0))  # 임의 포트
    sock.setblocking(False)
    return sock


async def punch_as_manager(relay_ws, agent_id: str,
                           timeout: float = PUNCH_TIMEOUT) -> Optional[UdpChannel]:
    """매니저 측 UDP 홀펀칭.

    Args:
        relay_ws: 릴레이 서버 WebSocket 연결
        agent_id: 대상 에이전트 ID
        timeout: 전체 타임아웃

    Returns:
        UdpChannel 또는 None (실패 시)
    """
    import json

    sock = _create_udp_socket()
    local_port = sock.getsockname()[1]
    punch_token = os.urandom(16)

    try:
        # 1. STUN 탐지
        logger.info(f"[UDP-Punch] STUN 탐지 시작 (로컬 포트: {local_port})")
        stun_result = await stun_discover(sock, timeout=3.0)

        if not stun_result:
            logger.warning("[UDP-Punch] STUN 탐지 실패")
            sock.close()
            return None

        my_ip, my_port = stun_result
        logger.info(f"[UDP-Punch] 내 공인 엔드포인트: {my_ip}:{my_port}")

        # 2. udp_offer 전송 (릴레이 경유)
        offer = json.dumps({
            'type': 'udp_offer',
            'target_agent': agent_id,
            'udp_ip': my_ip,
            'udp_port': my_port,
            'punch_token': punch_token.hex(),
        })
        await relay_ws.send(offer)
        logger.info(f"[UDP-Punch] udp_offer 전송 → {agent_id}")

        # 3. udp_answer 대기 (릴레이 경유)
        # answer는 _handle_relay_text에서 처리되어 future로 전달됨
        answer_future = asyncio.get_event_loop().create_future()

        # 임시 핸들러 등록
        _pending_answers[punch_token.hex()] = answer_future

        try:
            answer = await asyncio.wait_for(answer_future, timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning(f"[UDP-Punch] udp_answer 타임아웃 ({agent_id})")
            _pending_answers.pop(punch_token.hex(), None)
            sock.close()
            return None

        _pending_answers.pop(punch_token.hex(), None)

        peer_ip = answer['udp_ip']
        peer_port = answer['udp_port']
        logger.info(f"[UDP-Punch] 상대방 엔드포인트: {peer_ip}:{peer_port}")

        # 4. 홀펀칭 실행
        channel = await _do_punch(sock, (peer_ip, peer_port), punch_token, role=b'M')

        if channel:
            logger.info(f"[UDP-Punch] ★ 홀펀칭 성공! {agent_id} ({peer_ip}:{peer_port})")
            return channel
        else:
            logger.info(f"[UDP-Punch] 홀펀칭 실패 ({agent_id}) — 릴레이 폴백")
            sock.close()
            return None

    except Exception as e:
        logger.warning(f"[UDP-Punch] 매니저 홀펀칭 오류: {e}")
        try:
            sock.close()
        except Exception:
            pass
        return None


async def punch_as_agent(sock: socket.socket, peer_ip: str, peer_port: int,
                         punch_token: bytes) -> Optional[UdpChannel]:
    """에이전트 측 UDP 홀펀칭.

    매니저의 udp_offer를 수신한 후 호출.

    Args:
        sock: STUN 탐지에 사용한 같은 UDP 소켓
        peer_ip: 매니저의 공인 IP
        peer_port: 매니저의 공인 PORT
        punch_token: 핸드셰이크 토큰

    Returns:
        UdpChannel 또는 None
    """
    logger.info(f"[UDP-Punch] 에이전트 홀펀칭 시작 → {peer_ip}:{peer_port}")

    channel = await _do_punch(sock, (peer_ip, peer_port), punch_token, role=b'A')

    if channel:
        logger.info(f"[UDP-Punch] ★ 에이전트 홀펀칭 성공! ({peer_ip}:{peer_port})")
    else:
        logger.info(f"[UDP-Punch] 에이전트 홀펀칭 실패")
        try:
            sock.close()
        except Exception:
            pass

    return channel


async def _do_punch(sock: socket.socket, peer_addr: tuple[str, int],
                    token: bytes, role: bytes) -> Optional[UdpChannel]:
    """실제 홀펀칭 수행 — 양쪽이 동시에 호출.

    PUNCH_MAGIC + token + role 패킷을 반복 전송하면서
    상대방 패킷 수신을 기다림.

    Args:
        sock: UDP 소켓
        peer_addr: 상대방 (공인IP, 공인PORT)
        token: 16바이트 펀칭 토큰
        role: b'M'(매니저) 또는 b'A'(에이전트)

    Returns:
        UdpChannel 또는 None
    """
    loop = asyncio.get_event_loop()
    punch_packet = PUNCH_MAGIC + token + role
    received_punch = False
    actual_peer = None

    start = time.monotonic()

    while time.monotonic() - start < PUNCH_DURATION:
        # 전송
        try:
            sock.sendto(punch_packet, peer_addr)
        except Exception:
            pass

        # 수신 (짧은 대기)
        try:
            data, addr = await asyncio.wait_for(
                loop.sock_recvfrom(sock, 1024),
                timeout=PUNCH_INTERVAL,
            )

            # 홀펀칭 패킷 확인
            if len(data) >= len(PUNCH_MAGIC) + 16 + 1:
                if data[:4] == PUNCH_MAGIC and data[4:20] == token:
                    received_punch = True
                    actual_peer = addr
                    logger.debug(f"[UDP-Punch] 홀펀칭 패킷 수신! from {addr}")
                    # ACK 전송
                    ack_packet = PUNCH_ACK + token + role
                    sock.sendto(ack_packet, addr)
                    break
                elif data[:4] == PUNCH_ACK and data[4:20] == token:
                    received_punch = True
                    actual_peer = addr
                    logger.debug(f"[UDP-Punch] ACK 수신! from {addr}")
                    break

        except asyncio.TimeoutError:
            continue
        except Exception:
            continue

    if not received_punch or not actual_peer:
        return None

    # ACK 교환 확인 (추가 1초간 ACK 재전송)
    ack_packet = PUNCH_ACK + token + role
    for _ in range(10):
        try:
            sock.sendto(ack_packet, actual_peer)
            await asyncio.sleep(0.1)
        except Exception:
            pass

    # UdpChannel 생성
    channel = UdpChannel(sock, actual_peer, loop=loop)
    return channel


# 매니저 측 udp_answer 대기를 위한 전역 딕셔너리
# punch_token_hex → asyncio.Future
_pending_answers: dict[str, asyncio.Future] = {}


def handle_udp_answer(msg: dict):
    """릴레이에서 수신한 udp_answer를 대기 중인 Future에 전달.

    agent_server.py의 _handle_relay_text에서 호출.
    """
    token_hex = msg.get('punch_token', '')
    fut = _pending_answers.get(token_hex)
    if fut and not fut.done():
        fut.set_result(msg)
        logger.debug(f"[UDP-Punch] udp_answer 수신 (token={token_hex[:8]}...)")
