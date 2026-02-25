"""UDP 데이터 채널 — 홀펀칭된 UDP 소켓 위의 프레임 전송/수신

비디오 프레임: fire-and-forget (손실 허용, 재전송 없음)
제어 메시지: ACK + 재전송 (최대 3회)
MTU 초과 시 자동 분할/재조립
"""

import asyncio
import json
import struct
import time
import logging
import zlib
from typing import Optional, Callable

logger = logging.getLogger(__name__)

# 프레임 상수
MAGIC = 0x5743  # 'WC'
MAX_UDP_PAYLOAD = 1200  # MTU 안전 범위

# 프레임 타입
TYPE_THUMBNAIL = 0x01
TYPE_STREAM = 0x02      # MJPEG
TYPE_H264_KEY = 0x03
TYPE_H264_DELTA = 0x04
TYPE_CONTROL = 0x10     # JSON 제어 메시지 (ACK 필요)
TYPE_CONTROL_ACK = 0x11
TYPE_PING = 0xFE
TYPE_PONG = 0xFF

# 단일 패킷 헤더: magic(2) + seq(4) + type(1) + len(2) = 9 bytes
HEADER_SIZE = 9
# 분할 패킷 추가 헤더: chunk_idx(1) + total_chunks(1) = 2 bytes
CHUNK_HEADER_EXTRA = 2
# 단일 패킷 최대 페이로드
SINGLE_MAX_PAYLOAD = MAX_UDP_PAYLOAD - HEADER_SIZE
# 분할 패킷 최대 페이로드
CHUNK_MAX_PAYLOAD = MAX_UDP_PAYLOAD - HEADER_SIZE - CHUNK_HEADER_EXTRA

# ACK 타임아웃/재전송
ACK_TIMEOUT = 0.15   # 150ms
ACK_RETRIES = 3
PING_INTERVAL = 5.0
PING_TIMEOUT = 15.0


class UdpChannel:
    """홀펀칭된 UDP 위의 데이터 채널"""

    def __init__(self, sock, remote_addr: tuple[str, int], loop=None):
        self._sock = sock
        self._remote = remote_addr
        self._loop = loop or asyncio.get_event_loop()
        self._seq = 0
        self._running = True
        self._last_recv_time = time.monotonic()

        # ACK 대기
        self._ack_futures: dict[int, asyncio.Future] = {}

        # 분할 재조립 버퍼: seq → {chunk_idx: data, ...}
        self._reassembly: dict[int, dict] = {}
        self._reassembly_meta: dict[int, tuple] = {}  # seq → (total, type, timestamp)

        # 콜백
        self._on_control: Optional[Callable] = None
        self._on_video: Optional[Callable] = None

        # 태스크
        self._recv_task: Optional[asyncio.Task] = None
        self._ping_task: Optional[asyncio.Task] = None

    @property
    def is_alive(self) -> bool:
        return self._running and (time.monotonic() - self._last_recv_time < PING_TIMEOUT)

    def _next_seq(self) -> int:
        self._seq = (self._seq + 1) & 0xFFFFFFFF
        return self._seq

    def start(self, on_control=None, on_video=None):
        """수신 루프 시작"""
        self._on_control = on_control
        self._on_video = on_video
        self._recv_task = asyncio.ensure_future(self._recv_loop())
        self._ping_task = asyncio.ensure_future(self._ping_loop())

    async def close(self):
        """채널 종료"""
        self._running = False
        if self._recv_task:
            self._recv_task.cancel()
            try:
                await self._recv_task
            except (asyncio.CancelledError, Exception):
                pass
        if self._ping_task:
            self._ping_task.cancel()
            try:
                await self._ping_task
            except (asyncio.CancelledError, Exception):
                pass
        # ACK 대기 중인 future 취소
        for fut in self._ack_futures.values():
            if not fut.done():
                fut.cancel()
        self._ack_futures.clear()
        # 소켓 닫기
        try:
            self._sock.close()
        except Exception:
            pass

    # ──────────── 전송 ────────────

    def send_video(self, frame_type: int, data: bytes):
        """비디오 프레임 전송 (fire-and-forget, 손실 허용)"""
        if not self._running:
            return
        seq = self._next_seq()
        if len(data) <= SINGLE_MAX_PAYLOAD:
            self._send_packet(seq, frame_type, data)
        else:
            self._send_chunked(seq, frame_type, data)

    async def send_control(self, msg: dict) -> bool:
        """제어 메시지 전송 (ACK 대기, 재전송)

        Returns:
            True=ACK 수신, False=실패
        """
        if not self._running:
            return False
        payload = json.dumps(msg, ensure_ascii=False).encode('utf-8')
        seq = self._next_seq()

        for attempt in range(ACK_RETRIES + 1):
            fut = self._loop.create_future()
            self._ack_futures[seq] = fut

            if len(payload) <= SINGLE_MAX_PAYLOAD:
                self._send_packet(seq, TYPE_CONTROL, payload)
            else:
                self._send_chunked(seq, TYPE_CONTROL, payload)

            try:
                await asyncio.wait_for(fut, timeout=ACK_TIMEOUT)
                self._ack_futures.pop(seq, None)
                return True
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._ack_futures.pop(seq, None)
                if attempt < ACK_RETRIES:
                    logger.debug(f"[UDP] 제어 메시지 재전송 #{attempt + 1} (seq={seq})")

        logger.warning(f"[UDP] 제어 메시지 전송 실패 (seq={seq})")
        return False

    def _send_packet(self, seq: int, ptype: int, payload: bytes):
        """단일 UDP 패킷 전송"""
        # magic(2) + seq(4) + type(1) + len(2) + payload
        header = struct.pack('!HIBH', MAGIC, seq, ptype, len(payload))
        try:
            self._sock.sendto(header + payload, self._remote)
        except Exception as e:
            logger.debug(f"[UDP] 전송 오류: {e}")

    def _send_chunked(self, seq: int, ptype: int, data: bytes):
        """큰 데이터를 분할 전송"""
        total = (len(data) + CHUNK_MAX_PAYLOAD - 1) // CHUNK_MAX_PAYLOAD
        if total > 255:
            logger.warning(f"[UDP] 데이터 너무 큼: {len(data)} bytes, {total} chunks")
            return

        for i in range(total):
            offset = i * CHUNK_MAX_PAYLOAD
            chunk = data[offset:offset + CHUNK_MAX_PAYLOAD]
            # 분할 헤더: magic(2) + seq(4) + type(1) + len(2) + chunk_idx(1) + total(1) + payload
            header = struct.pack('!HIBHBB', MAGIC, seq, ptype | 0x80,
                                 len(chunk) + CHUNK_HEADER_EXTRA, i, total)
            try:
                self._sock.sendto(header + chunk, self._remote)
            except Exception:
                pass

    def _send_ack(self, seq: int):
        """ACK 전송"""
        header = struct.pack('!HIBH', MAGIC, seq, TYPE_CONTROL_ACK, 0)
        try:
            self._sock.sendto(header, self._remote)
        except Exception:
            pass

    def _send_ping(self):
        """PING 전송"""
        seq = self._next_seq()
        header = struct.pack('!HIBH', MAGIC, seq, TYPE_PING, 0)
        try:
            self._sock.sendto(header, self._remote)
        except Exception:
            pass

    def _send_pong(self, seq: int):
        """PONG 응답"""
        header = struct.pack('!HIBH', MAGIC, seq, TYPE_PONG, 0)
        try:
            self._sock.sendto(header, self._remote)
        except Exception:
            pass

    # ──────────── 수신 ────────────

    async def _recv_loop(self):
        """UDP 수신 루프"""
        while self._running:
            try:
                data = await asyncio.wait_for(
                    self._loop.sock_recv(self._sock, 65536),
                    timeout=1.0,
                )
                if not data or len(data) < HEADER_SIZE:
                    continue

                self._last_recv_time = time.monotonic()
                self._process_packet(data)

            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                return
            except Exception as e:
                if self._running:
                    logger.debug(f"[UDP] 수신 오류: {e}")
                    await asyncio.sleep(0.1)

    def _process_packet(self, data: bytes):
        """수신 패킷 처리"""
        magic, seq, ptype, plen = struct.unpack_from('!HIBH', data, 0)
        if magic != MAGIC:
            return

        payload = data[HEADER_SIZE:]

        # 분할 패킷?
        if ptype & 0x80:
            actual_type = ptype & 0x7F
            if len(payload) < 2:
                return
            chunk_idx, total_chunks = payload[0], payload[1]
            chunk_data = payload[2:]
            self._handle_chunk(seq, actual_type, chunk_idx, total_chunks, chunk_data)
            return

        # 단일 패킷
        payload = payload[:plen]

        if ptype == TYPE_CONTROL_ACK:
            fut = self._ack_futures.get(seq)
            if fut and not fut.done():
                fut.set_result(True)
        elif ptype == TYPE_PING:
            self._send_pong(seq)
        elif ptype == TYPE_PONG:
            pass  # 킵얼라이브 확인 (last_recv_time 이미 갱신)
        elif ptype == TYPE_CONTROL:
            self._send_ack(seq)
            self._dispatch_control(payload)
        elif ptype in (TYPE_THUMBNAIL, TYPE_STREAM, TYPE_H264_KEY, TYPE_H264_DELTA):
            self._dispatch_video(ptype, payload)

    def _handle_chunk(self, seq: int, ptype: int, idx: int, total: int, data: bytes):
        """분할 패킷 재조립"""
        if seq not in self._reassembly:
            self._reassembly[seq] = {}
            self._reassembly_meta[seq] = (total, ptype, time.monotonic())

        self._reassembly[seq][idx] = data

        # 모든 청크 수신?
        if len(self._reassembly[seq]) == total:
            full = b''
            for i in range(total):
                chunk = self._reassembly[seq].get(i)
                if chunk is None:
                    # 누락된 청크 — 폐기
                    del self._reassembly[seq]
                    del self._reassembly_meta[seq]
                    return
                full += chunk

            del self._reassembly[seq]
            del self._reassembly_meta[seq]

            if ptype == TYPE_CONTROL:
                self._send_ack(seq)
                self._dispatch_control(full)
            elif ptype in (TYPE_THUMBNAIL, TYPE_STREAM, TYPE_H264_KEY, TYPE_H264_DELTA):
                self._dispatch_video(ptype, full)

        # 오래된 재조립 버퍼 정리
        now = time.monotonic()
        expired = [s for s, (_, _, ts) in self._reassembly_meta.items() if now - ts > 2.0]
        for s in expired:
            self._reassembly.pop(s, None)
            self._reassembly_meta.pop(s, None)

    def _dispatch_control(self, payload: bytes):
        """제어 메시지 콜백 호출"""
        if self._on_control:
            try:
                msg = json.loads(payload.decode('utf-8'))
                self._on_control(msg)
            except Exception as e:
                logger.debug(f"[UDP] 제어 메시지 파싱 오류: {e}")

    def _dispatch_video(self, frame_type: int, data: bytes):
        """비디오 프레임 콜백 호출"""
        if self._on_video:
            try:
                self._on_video(frame_type, data)
            except Exception as e:
                logger.debug(f"[UDP] 비디오 프레임 처리 오류: {e}")

    async def _ping_loop(self):
        """킵얼라이브 PING"""
        while self._running:
            try:
                await asyncio.sleep(PING_INTERVAL)
                if not self._running:
                    return
                self._send_ping()

                # 타임아웃 체크
                if not self.is_alive:
                    logger.warning("[UDP] 킵얼라이브 타임아웃 — 채널 종료")
                    self._running = False
                    return
            except asyncio.CancelledError:
                return
