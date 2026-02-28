"""STUN 클라이언트 — NAT 공인 UDP 엔드포인트 탐지 (RFC 5389)

UDP 소켓의 NAT 매핑된 공인 IP:PORT를 STUN 서버에 질의하여 반환.
NAT 홀펀칭의 첫 단계로, 양쪽의 공인 엔드포인트를 알아내야 시그널링 가능.
"""

import asyncio
import os
import struct
import socket
import logging

logger = logging.getLogger(__name__)

# STUN 상수
STUN_MAGIC_COOKIE = 0x2112A442
STUN_BINDING_REQUEST = 0x0001
STUN_BINDING_RESPONSE = 0x0101
ATTR_MAPPED_ADDRESS = 0x0001
ATTR_XOR_MAPPED_ADDRESS = 0x0020

# 공용 STUN 서버 목록
STUN_SERVERS = [
    ('stun.l.google.com', 19302),
    ('stun1.l.google.com', 19302),
    ('stun2.l.google.com', 19302),
    ('stun.cloudflare.com', 3478),
    ('stun.stunprotocol.org', 3478),
]


def _build_binding_request() -> tuple[bytes, bytes]:
    """STUN Binding Request 패킷 생성.

    Returns:
        (packet, transaction_id)
    """
    txn_id = os.urandom(12)
    # Header: type(2) + length(2) + magic(4) + txn_id(12) = 20 bytes
    header = struct.pack('!HHI', STUN_BINDING_REQUEST, 0, STUN_MAGIC_COOKIE) + txn_id
    return header, txn_id


def _parse_binding_response(data: bytes, txn_id: bytes) -> tuple[str, int] | None:
    """STUN Binding Response에서 공인 IP:PORT 추출.

    Returns:
        (ip, port) 또는 None
    """
    if len(data) < 20:
        return None

    msg_type, msg_len, magic = struct.unpack_from('!HHI', data, 0)
    resp_txn = data[8:20]

    if msg_type != STUN_BINDING_RESPONSE:
        return None
    if magic != STUN_MAGIC_COOKIE:
        return None
    if resp_txn != txn_id:
        return None

    # 속성 파싱
    offset = 20
    while offset + 4 <= len(data):
        attr_type, attr_len = struct.unpack_from('!HH', data, offset)
        offset += 4
        if offset + attr_len > len(data):
            break

        if attr_type == ATTR_XOR_MAPPED_ADDRESS:
            result = _parse_xor_mapped(data[offset:offset + attr_len], txn_id)
            if result:
                return result
        elif attr_type == ATTR_MAPPED_ADDRESS:
            result = _parse_mapped(data[offset:offset + attr_len])
            if result:
                return result

        # 4바이트 정렬
        offset += attr_len
        if attr_len % 4:
            offset += 4 - (attr_len % 4)

    return None


def _parse_xor_mapped(attr_data: bytes, txn_id: bytes) -> tuple[str, int] | None:
    """XOR-MAPPED-ADDRESS 속성 파싱"""
    if len(attr_data) < 8:
        return None

    family = attr_data[1]
    xor_port = struct.unpack_from('!H', attr_data, 2)[0]
    port = xor_port ^ (STUN_MAGIC_COOKIE >> 16)

    if family == 0x01:  # IPv4
        xor_ip = struct.unpack_from('!I', attr_data, 4)[0]
        ip_int = xor_ip ^ STUN_MAGIC_COOKIE
        ip = socket.inet_ntoa(struct.pack('!I', ip_int))
        return ip, port

    return None


def _parse_mapped(attr_data: bytes) -> tuple[str, int] | None:
    """MAPPED-ADDRESS 속성 파싱 (폴백)"""
    if len(attr_data) < 8:
        return None

    family = attr_data[1]
    port = struct.unpack_from('!H', attr_data, 2)[0]

    if family == 0x01:  # IPv4
        ip = socket.inet_ntoa(attr_data[4:8])
        return ip, port

    return None


async def stun_discover(sock: socket.socket,
                        timeout: float = 3.0,
                        servers: list | None = None) -> tuple[str, int] | None:
    """UDP 소켓의 NAT 매핑된 공인 IP:PORT 탐지.

    Args:
        sock: 바인딩된 UDP 소켓 (non-blocking)
        timeout: STUN 응답 대기 시간
        servers: STUN 서버 목록 [(host, port), ...]

    Returns:
        (공인IP, 공인PORT) 또는 None
    """
    if servers is None:
        servers = STUN_SERVERS

    loop = asyncio.get_event_loop()
    packet, txn_id = _build_binding_request()

    for host, port in servers:
        try:
            # DNS 해석
            addr_info = await loop.getaddrinfo(host, port, family=socket.AF_INET,
                                                type=socket.SOCK_DGRAM)
            if not addr_info:
                continue
            server_addr = addr_info[0][4]

            # Binding Request 전송
            sock.sendto(packet, server_addr)

            # 응답 대기
            try:
                data = await asyncio.wait_for(
                    loop.sock_recv(sock, 1024),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                logger.debug(f"[STUN] {host}:{port} 타임아웃")
                continue

            result = _parse_binding_response(data, txn_id)
            if result:
                logger.info(f"[STUN] 공인 엔드포인트: {result[0]}:{result[1]} (via {host})")
                return result

        except Exception as e:
            logger.debug(f"[STUN] {host}:{port} 오류: {e}")
            continue

    logger.warning("[STUN] 모든 STUN 서버 실패")
    return None


async def stun_detect_nat_type(sock: socket.socket,
                                timeout: float = 3.0
                                ) -> tuple[str, tuple[str, int], tuple[str, int] | None]:
    """같은 소켓으로 여러 STUN 서버에 **동시** 질의하여 NAT 타입 감지.

    포트가 동일하면 non-symmetric (홀펀칭 가능),
    포트가 다르면 symmetric NAT (포트 예측 필요).

    v3.3.5: 순차 질의 → 병렬 질의로 개선 (속도 + 신뢰성)

    Args:
        sock: 바인딩된 UDP 소켓 (non-blocking)
        timeout: 전체 응답 대기 시간

    Returns:
        (nat_type, endpoint1, endpoint2)
        nat_type: "full_cone" | "symmetric" | "unknown"
        endpoint1: (ip, port) — 첫 번째 STUN 결과
        endpoint2: (ip, port) | None — 두 번째 STUN 결과
    """
    loop = asyncio.get_event_loop()
    results: list[tuple[str, int]] = []
    seen_servers: set[str] = set()  # 같은 서버 결과 중복 방지

    # 서로 다른 STUN 서버 목록 (다양한 공급자)
    server_list = [
        ('stun.l.google.com', 19302),
        ('stun.cloudflare.com', 3478),
        ('stun1.l.google.com', 19302),
        ('stun2.l.google.com', 19302),
        ('stun.stunprotocol.org', 3478),
    ]

    # 각 서버별 txn_id 매핑 (동시 전송용)
    txn_map: dict[bytes, str] = {}  # txn_id → server_host

    # 1단계: 모든 STUN 서버에 동시 전송
    for host, port in server_list:
        packet, txn_id = _build_binding_request()
        try:
            addr_info = await loop.getaddrinfo(host, port,
                                                family=socket.AF_INET,
                                                type=socket.SOCK_DGRAM)
            if not addr_info:
                continue
            server_addr = addr_info[0][4]
            sock.sendto(packet, server_addr)
            txn_map[txn_id] = host
        except Exception as e:
            logger.debug(f"[STUN-NAT] {host}:{port} 전송 실패: {e}")
            continue

    if not txn_map:
        return ("unknown", ("", 0), None)

    # 2단계: 응답 수집 (2개 이상 또는 타임아웃까지)
    deadline = asyncio.get_event_loop().time() + timeout

    while len(results) < 2:
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            break
        try:
            data = await asyncio.wait_for(
                loop.sock_recv(sock, 1024), timeout=remaining)
        except asyncio.TimeoutError:
            break
        except Exception:
            break

        # 수신된 패킷에서 txn_id 추출하여 매칭
        if len(data) < 20:
            continue
        resp_txn = data[8:20]
        server_host = txn_map.get(resp_txn)
        if not server_host or server_host in seen_servers:
            continue

        result = _parse_binding_response(data, resp_txn)
        if result:
            results.append(result)
            seen_servers.add(server_host)
            logger.debug(f"[STUN-NAT] {server_host}: {result[0]}:{result[1]}")

    if len(results) == 0:
        return ("unknown", ("", 0), None)
    if len(results) == 1:
        return ("unknown", results[0], None)

    ip1, port1 = results[0]
    ip2, port2 = results[1]

    if ip1 == ip2 and port1 == port2:
        nat_type = "full_cone"
    else:
        nat_type = "symmetric"

    logger.info(f"[STUN-NAT] NAT 타입: {nat_type} "
                f"(ep1={ip1}:{port1}, ep2={ip2}:{port2})")

    return (nat_type, results[0], results[1])
