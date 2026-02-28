"""P2P 연결 매니저 — 각 에이전트에 직접 WS 접속

v3.0.0: 서버 릴레이 → P2P 직접 연결
- 에이전트별 독립 WS 연결 (1 에이전트 = 1 WebSocket)
- 연결 우선순위: WAN(ip1) → UDP 홀펀칭 → 서버 릴레이(폴백)
- 시그널 인터페이스 100% 기존 호환 (UI 변경 없음)

클래스명 AgentServer 유지 (UI 코드 변경 최소화).
"""

import asyncio
import json
import base64
import logging
import threading
import os
from enum import Enum
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from PyQt6.QtCore import QObject, pyqtSignal

logger = logging.getLogger(__name__)

try:
    import websockets
    WEBSOCKETS_AVAILABLE = True
except ImportError:
    WEBSOCKETS_AVAILABLE = False
    logger.warning("websockets 미설치 — 에이전트 기능 비활성화")

# 서버 릴레이용 agent_id 패딩 (폴백 모드)
AGENT_ID_LEN = 32


def _pad_agent_id(agent_id: str) -> bytes:
    return agent_id.encode('utf-8')[:AGENT_ID_LEN].ljust(AGENT_ID_LEN, b'\x00')


def _unpad_agent_id(data: bytes) -> str:
    return data[:AGENT_ID_LEN].rstrip(b'\x00').decode('utf-8', errors='replace')


class ConnectionMode(Enum):
    LAN = "lan"               # ip2(사설IP) 직접 연결
    UDP_P2P = "udp_p2p"       # UDP 홀펀칭 P2P (포트포워딩 불필요)
    WAN = "wan"               # ip1(공인IP) 직접 연결
    RELAY = "relay"           # 서버 릴레이 폴백
    DISCONNECTED = "disconnected"


@dataclass
class AgentConnection:
    """에이전트별 연결 상태"""
    agent_id: str
    ws: Optional[object] = None
    mode: ConnectionMode = ConnectionMode.DISCONNECTED
    ip_private: str = ""      # ip2 (LAN)
    ip_public: str = ""       # ip1 (WAN)
    ws_port: int = 21350
    info: dict = field(default_factory=dict)
    udp_channel: Optional[object] = field(default=None, repr=False)  # UDP P2P 채널
    _recv_task: Optional[asyncio.Task] = field(default=None, repr=False)
    _upgrading: bool = field(default=False, repr=False)  # P2P 업그레이드 진행 중
    _connecting: bool = field(default=False, repr=False)  # _connect_cascade 실행 중
    _last_upgrade_fail: float = field(default=0.0, repr=False)  # 마지막 업그레이드 실패 시각


class AgentServer(QObject):
    """P2P 연결 매니저 — 각 에이전트에 직접 WS 접속

    v3.0.0: 서버 릴레이 → P2P 직접 연결
    - 에이전트별 독립 WS 연결 (1 에이전트 = 1 WebSocket)
    - 연결 우선순위: WAN(ip1) → UDP 홀펀칭 → 서버 릴레이(폴백)
    - 시그널 인터페이스 100% 기존 호환
    """

    # ★ 시그널 100% 동일 유지 (UI 변경 없음)
    agent_connected = pyqtSignal(str, str)            # agent_id, remote_ip
    agent_disconnected = pyqtSignal(str)               # agent_id
    thumbnail_received = pyqtSignal(str, bytes)        # agent_id, jpeg_data
    screen_frame_received = pyqtSignal(str, bytes)     # agent_id, jpeg_data
    h264_frame_received = pyqtSignal(str, int, bytes)  # agent_id, header, raw_data
    stream_started = pyqtSignal(str, dict)             # agent_id, info_dict
    clipboard_received = pyqtSignal(str, str, object)  # agent_id, format, data
    file_progress = pyqtSignal(str, int, int)          # agent_id, sent, total
    file_complete = pyqtSignal(str, str)               # agent_id, remote_path
    command_result = pyqtSignal(str, str, str, int)    # agent_id, command, output, returncode
    connection_mode_changed = pyqtSignal(str, str)     # agent_id, mode (NEW)
    agent_info_received = pyqtSignal(str, dict)        # agent_id, info_dict (system_info 수신)
    update_status_received = pyqtSignal(str, dict)     # agent_id, status_dict
    latency_measured = pyqtSignal(str, int)             # agent_id, ms
    monitors_received = pyqtSignal(str, list)           # agent_id, monitors_list
    performance_received = pyqtSignal(str, dict)        # agent_id, {cpu, ram, disk}
    audio_received = pyqtSignal(str, bytes)             # agent_id, pcm_data

    CHUNK_SIZE = 64 * 1024  # 64KB

    # 바이너리 프레임 헤더
    HEADER_THUMBNAIL = 0x01
    HEADER_STREAM = 0x02
    HEADER_H264_KEYFRAME = 0x03
    HEADER_H264_DELTA = 0x04
    HEADER_AUDIO = 0x05

    def __init__(self):
        super().__init__()
        self._connections: Dict[str, AgentConnection] = {}
        self._relay_ws: Optional[object] = None  # 서버 릴레이 WS (폴백용)
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._server_url: str = ""    # 서버 REST/WS URL (폴백용)
        self._token: str = ""
        self._manager_id: str = ""    # 매니저 식별자
        # 연결 타임아웃 설정
        self._timeout_lan: int = 3
        self._timeout_wan: int = 5
        self._reconnect_interval: int = 10
        # 핑 타임스탬프 (agent_id → send_time)
        self._ping_times: Dict[str, float] = {}

    @property
    def connected_count(self) -> int:
        return sum(1 for c in self._connections.values()
                   if c.mode != ConnectionMode.DISCONNECTED)

    def get_connected_agents(self) -> List[str]:
        return [aid for aid, c in self._connections.items()
                if c.mode != ConnectionMode.DISCONNECTED]

    def is_agent_connected(self, agent_id: str) -> bool:
        conn = self._connections.get(agent_id)
        if not conn or conn.mode == ConnectionMode.DISCONNECTED:
            return False
        if conn.mode == ConnectionMode.UDP_P2P:
            return conn.udp_channel is not None and conn.udp_channel.is_alive
        return conn.ws is not None

    def get_agent_info(self, agent_id: str) -> Optional[dict]:
        conn = self._connections.get(agent_id)
        return conn.info if conn else None

    def get_connection_mode(self, agent_id: str) -> str:
        """에이전트의 현재 연결 모드 반환"""
        conn = self._connections.get(agent_id)
        return conn.mode.value if conn else "disconnected"

    # ==================== 연결 수명주기 ====================

    def start_connection(self, server_url: str, token: str):
        """P2P 연결 매니저 시작 (백그라운드 스레드)

        Args:
            server_url: 서버 URL (폴백 릴레이 + REST API용)
            token: JWT 토큰
        """
        if not WEBSOCKETS_AVAILABLE:
            logger.error("[P2P] websockets 미설치 — 연결 불가")
            return

        if self._thread and self._thread.is_alive():
            return

        self._server_url = server_url
        self._token = token
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop, daemon=True, name='AgentServer-P2P'
        )
        self._thread.start()
        logger.info(f"[P2P] 연결 매니저 시작 (서버: {server_url})")

    def connect_to_agent(self, agent_id: str, ip_private: str,
                         ip_public: str, ws_port: int = 21350):
        """에이전트에 P2P 연결 시도 (WAN→UDP홀펀칭→릴레이)

        중복 호출 안전: 같은 agent_id에 대해 여러 번 호출해도
        기존 AgentConnection 객체를 재사용하여 race condition 방지.
        """
        conn = self._connections.get(agent_id)

        if not conn:
            # 신규 에이전트 — 새 연결 객체 생성
            conn = AgentConnection(
                agent_id=agent_id,
                ip_private=ip_private,
                ip_public=ip_public,
                ws_port=ws_port,
            )
            self._connections[agent_id] = conn
        else:
            # 기존 에이전트 — IP 정보만 업데이트 (빈 값으로 덮어쓰지 않음)
            if ip_private:
                conn.ip_private = ip_private
            if ip_public:
                conn.ip_public = ip_public
            if ws_port:
                conn.ws_port = ws_port

        # 이미 연결됨 → P2P 업그레이드만 시도
        if conn.mode != ConnectionMode.DISCONNECTED:
            if (conn.mode == ConnectionMode.RELAY
                    and not conn._upgrading
                    and (ip_private or ip_public)):
                if self._loop and self._loop.is_running():
                    asyncio.run_coroutine_threadsafe(
                        self._try_p2p_upgrade(agent_id), self._loop
                    )
            return

        # DISCONNECTED + cascade 이미 진행 중 → IP만 업데이트하고 스킵
        if conn._connecting:
            logger.debug(f"[P2P] {agent_id} cascade 이미 진행 중 — IP 업데이트만")
            return

        # cascade 시작
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(
                self._connect_cascade(agent_id), self._loop
            )

    def disconnect_agent(self, agent_id: str):
        """에이전트 연결 해제"""
        conn = self._connections.get(agent_id)
        if conn and conn.ws and self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(
                self._close_connection(agent_id), self._loop
            )

    def stop_connection(self):
        """전체 연결 매니저 종료"""
        self._stop_event.set()

        # 모든 에이전트 연결 해제 (UDP 채널 포함)
        if self._loop and self._loop.is_running():
            for agent_id in list(self._connections.keys()):
                try:
                    asyncio.run_coroutine_threadsafe(
                        self._close_connection(agent_id), self._loop
                    ).result(timeout=3)
                except (TimeoutError, RuntimeError) as e:
                    logger.debug(f"[P2P] {agent_id} 종료 중 연결 해제 실패: {type(e).__name__}")
                except Exception as e:
                    logger.debug(f"[P2P] {agent_id} 종료 중 예외: {type(e).__name__}: {e}")

        # 릴레이 연결 해제
        if self._relay_ws and self._loop and self._loop.is_running():
            try:
                asyncio.run_coroutine_threadsafe(self._relay_ws.close(), self._loop)
            except RuntimeError:
                logger.debug("[Relay] 종료 중 릴레이 해제 실패: 이벤트 루프 종료")
            except Exception as e:
                logger.debug(f"[Relay] 종료 중 릴레이 해제 실패: {type(e).__name__}")

        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)

        for agent_id in list(self._connections.keys()):
            self.agent_disconnected.emit(agent_id)
        self._connections.clear()

    # 하위 호환
    def start_server(self):
        logger.warning("[P2P] start_server() → start_connection() 사용 권장")

    def stop_server(self):
        self.stop_connection()

    # ==================== 에이전트에 명령 전송 ====================

    def request_thumbnail(self, agent_id: str):
        self._send_to_agent(agent_id, {'type': 'request_thumbnail'})

    def start_streaming(self, agent_id: str, fps: int = 30, quality: int = 80,
                        codec: str = 'h264', keyframe_interval: int = 60):
        self._send_to_agent(agent_id, {
            'type': 'start_stream', 'fps': fps, 'quality': quality,
            'codec': codec, 'keyframe_interval': keyframe_interval,
        })

    def stop_streaming(self, agent_id: str):
        self._send_to_agent(agent_id, {'type': 'stop_stream'})

    def update_streaming(self, agent_id: str, fps: int = 15, quality: int = 60):
        self._send_to_agent(agent_id, {
            'type': 'update_stream', 'fps': fps, 'quality': quality,
        })

    def send_special_key(self, agent_id: str, key_combo: str):
        self._send_to_agent(agent_id, {
            'type': 'special_key', 'combo': key_combo,
        })

    def request_keyframe(self, agent_id: str):
        self._send_to_agent(agent_id, {'type': 'request_keyframe'})

    def start_thumbnail_push(self, agent_id: str, interval: float = 1.0):
        self._send_to_agent(agent_id, {
            'type': 'start_thumbnail_push', 'interval': interval,
        })

    def stop_thumbnail_push(self, agent_id: str):
        self._send_to_agent(agent_id, {'type': 'stop_thumbnail_push'})

    def send_key_event(self, agent_id: str, key: str, action: str,
                       modifiers: list = None):
        self._send_to_agent(agent_id, {
            'type': 'key_event', 'key': key,
            'action': action, 'modifiers': modifiers or [],
        })

    def send_mouse_event(self, agent_id: str, x: int, y: int,
                         button: str = 'none', action: str = 'move',
                         scroll_delta: int = 0):
        self._send_to_agent(agent_id, {
            'type': 'mouse_event', 'x': x, 'y': y,
            'button': button, 'action': action,
            'scroll_delta': scroll_delta,
        })

    def send_clipboard_text(self, agent_id: str, text: str):
        self._send_to_agent(agent_id, {
            'type': 'clipboard', 'format': 'text', 'data': text,
        })

    def send_clipboard_image(self, agent_id: str, image_data: bytes):
        self._send_to_agent(agent_id, {
            'type': 'clipboard', 'format': 'image',
            'data': base64.b64encode(image_data).decode('ascii'),
        })

    def send_file(self, agent_id: str, filepath: str):
        if not self.is_agent_connected(agent_id):
            return

        def _do_send():
            try:
                asyncio.run_coroutine_threadsafe(
                    self._send_file_async(agent_id, filepath), self._loop
                ).result(timeout=300)
            except Exception as e:
                logger.error(f"파일 전송 실패 [{agent_id}]: {e}")

        threading.Thread(target=_do_send, daemon=True).start()

    def execute_command(self, agent_id: str, command: str):
        self._send_to_agent(agent_id, {'type': 'execute', 'command': command})

    # ==================== 브로드캐스트 ====================

    def broadcast_key_event(self, agent_ids: List[str], key: str,
                            action: str, modifiers: list = None):
        for agent_id in agent_ids:
            self.send_key_event(agent_id, key, action, modifiers)

    def broadcast_mouse_event(self, agent_ids: List[str], x: int, y: int,
                              button: str = 'none', action: str = 'move',
                              scroll_delta: int = 0):
        for agent_id in agent_ids:
            self.send_mouse_event(agent_id, x, y, button, action, scroll_delta)

    def broadcast_file(self, agent_ids: List[str], filepath: str):
        for agent_id in agent_ids:
            self.send_file(agent_id, filepath)

    def broadcast_command(self, agent_ids: List[str], command: str):
        for agent_id in agent_ids:
            self.execute_command(agent_id, command)

    def ping_agent(self, agent_id: str):
        """에이전트에 ping 전송 (RTT 측정용)"""
        import time
        self._ping_times[agent_id] = time.time()
        self._send_to_agent(agent_id, {'type': 'ping', 'ts': self._ping_times[agent_id]})

    def ping_all_agents(self):
        """모든 연결된 에이전트에 ping 전송"""
        for agent_id in self.get_connected_agents():
            self.ping_agent(agent_id)

    def start_audio_stream(self, agent_id: str, sample_rate: int = 16000, channels: int = 1):
        """에이전트에 오디오 스트리밍 시작 요청"""
        self._send_to_agent(agent_id, {
            'type': 'start_audio',
            'sample_rate': sample_rate,
            'channels': channels,
        })

    def stop_audio_stream(self, agent_id: str):
        """에이전트에 오디오 스트리밍 중지 요청"""
        self._send_to_agent(agent_id, {'type': 'stop_audio'})

    def request_performance(self, agent_id: str):
        """에이전트의 성능 정보(CPU/RAM/Disk) 요청"""
        self._send_to_agent(agent_id, {'type': 'get_performance'})

    def request_all_performance(self):
        """모든 연결된 에이전트에 성능 정보 요청"""
        for agent_id in self.get_connected_agents():
            self.request_performance(agent_id)

    def request_monitors(self, agent_id: str):
        """에이전트의 모니터 목록 요청"""
        self._send_to_agent(agent_id, {'type': 'get_monitors'})

    def select_monitor(self, agent_id: str, index: int):
        """에이전트의 캡처 모니터 변경"""
        self._send_to_agent(agent_id, {'type': 'select_monitor', 'index': index})

    def send_power_action(self, agent_id: str, action: str):
        """에이전트에 전원 관리 명령 전송 (shutdown/restart/logoff/sleep)"""
        self._send_to_agent(agent_id, {'type': 'power_action', 'action': action})

    def send_update_request(self, agent_id: str):
        """에이전트에 원격 업데이트 요청 전송"""
        self._send_to_agent(agent_id, {'type': 'update_request'})

    # ==================== 내부 구현 — 전송 ====================

    def _send_to_agent(self, agent_id: str, msg_dict: dict):
        """에이전트에 JSON 전송 (UDP P2P / P2P 직접 / 릴레이)"""
        conn = self._connections.get(agent_id)
        if not conn or not self._loop or not self._loop.is_running():
            return

        # UDP P2P: UdpChannel로 제어 메시지 전송
        if conn.mode == ConnectionMode.UDP_P2P and conn.udp_channel:
            try:
                asyncio.run_coroutine_threadsafe(
                    conn.udp_channel.send_control(msg_dict), self._loop
                )
            except Exception as e:
                logger.warning(f"[UDP] {agent_id} 제어 전송 실패: {e}")
            return

        if not conn.ws:
            return

        # 릴레이 모드: ws가 닫혔으면 전송 스킵
        if conn.mode == ConnectionMode.RELAY:
            if not self._relay_ws or conn.ws != self._relay_ws:
                logger.debug(f"[P2P] {agent_id} 릴레이 WS 만료 — 전송 스킵")
                return
            msg_dict['target_agent'] = agent_id

        msg = json.dumps(msg_dict)
        try:
            future = asyncio.run_coroutine_threadsafe(conn.ws.send(msg), self._loop)
            future.add_done_callback(
                lambda f: logger.warning(f"[P2P] {agent_id} 전송 실패: {f.exception()}")
                if not f.cancelled() and f.exception() else None
            )
        except Exception as e:
            logger.warning(f"[P2P] {agent_id} 전송 예약 실패: {e}")

    def _send_binary_to_agent(self, agent_id: str, data: bytes):
        """에이전트에 바이너리 전송 (UDP P2P / P2P 직접 / 릴레이)"""
        conn = self._connections.get(agent_id)
        if not conn or not self._loop or not self._loop.is_running():
            return

        # UDP P2P: UdpChannel로 비디오 전송
        if conn.mode == ConnectionMode.UDP_P2P and conn.udp_channel:
            if len(data) < 1:
                return
            header = data[0]
            payload = data[1:]
            conn.udp_channel.send_video(header, payload)
            return

        if not conn.ws:
            return

        if conn.mode == ConnectionMode.RELAY:
            # 릴레이: 32B agent_id prefix 추가
            prefixed = _pad_agent_id(agent_id) + data
            try:
                asyncio.run_coroutine_threadsafe(conn.ws.send(prefixed), self._loop)
            except RuntimeError:
                logger.debug(f"[Relay] {agent_id} 바이너리 전송 실패: 이벤트 루프 종료")
            except Exception as e:
                logger.debug(f"[Relay] {agent_id} 바이너리 전송 실패: {type(e).__name__}")
        else:
            # P2P: 직접 전송 (prefix 없음)
            try:
                asyncio.run_coroutine_threadsafe(conn.ws.send(data), self._loop)
            except RuntimeError:
                logger.debug(f"[P2P] {agent_id} 바이너리 전송 실패: 이벤트 루프 종료")
            except Exception as e:
                logger.debug(f"[P2P] {agent_id} 바이너리 전송 실패: {type(e).__name__}")

    async def _send_file_async(self, agent_id: str, filepath: str):
        """파일 전송 코루틴"""
        conn = self._connections.get(agent_id)
        if not conn or not conn.ws:
            return

        filename = os.path.basename(filepath)
        filesize = os.path.getsize(filepath)

        # file_start
        start_msg = {'type': 'file_start', 'name': filename, 'size': filesize}
        if conn.mode == ConnectionMode.RELAY:
            start_msg['target_agent'] = agent_id
        await conn.ws.send(json.dumps(start_msg))

        # 파일 청크 전송
        sent = 0
        with open(filepath, 'rb') as f:
            while True:
                chunk = f.read(self.CHUNK_SIZE)
                if not chunk:
                    break
                if conn.mode == ConnectionMode.RELAY:
                    await conn.ws.send(_pad_agent_id(agent_id) + chunk)
                else:
                    await conn.ws.send(chunk)
                sent += len(chunk)
                self.file_progress.emit(agent_id, sent, filesize)

        # file_end
        end_msg = {'type': 'file_end', 'name': filename}
        if conn.mode == ConnectionMode.RELAY:
            end_msg['target_agent'] = agent_id
        await conn.ws.send(json.dumps(end_msg))

    # ==================== 내부 구현 — 연결 ====================

    def _run_loop(self):
        """asyncio 이벤트 루프 실행 (스레드)"""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._main_loop())
        except Exception as e:
            if not self._stop_event.is_set():
                logger.error(f"[P2P] 이벤트 루프 오류: {e}")
        finally:
            try:
                self._loop.close()
            except Exception:
                pass
            self._loop = None

    async def _main_loop(self):
        """메인 이벤트 루프 — 종료까지 대기"""
        # 서버 릴레이도 백그라운드로 연결 (폴백용)
        relay_task = asyncio.create_task(self._relay_connect_loop())

        try:
            while not self._stop_event.is_set():
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass
        finally:
            relay_task.cancel()
            try:
                await relay_task
            except (asyncio.CancelledError, Exception):
                pass

    async def _connect_cascade(self, agent_id: str):
        """릴레이 우선 즉시 연결 → 백그라운드 P2P 업그레이드 (TeamViewer 방식)

        포트포워딩 없이도 즉시 연결되며, 가능하면 P2P로 자동 전환.
        """
        conn = self._connections.get(agent_id)
        if not conn:
            return

        # 이미 연결됨 또는 다른 cascade 실행 중
        if conn.mode != ConnectionMode.DISCONNECTED or conn._connecting:
            return

        conn._connecting = True
        logger.info(f"[P2P] {agent_id} 연결 시작 (릴레이 우선 → P2P 백그라운드 업그레이드)")

        # ── 1단계: 릴레이 즉시 연결 (포트포워딩 불필요) ──
        # relay가 아직 접속 안 된 경우 최대 3초 대기
        if not self._relay_ws:
            logger.info(f"[P2P] {agent_id} 릴레이 대기 중...")
            for _ in range(30):
                await asyncio.sleep(0.1)
                if self._relay_ws or conn.mode != ConnectionMode.DISCONNECTED:
                    break

        # agent_connected 핸들러가 이미 연결했으면 완료
        if conn.mode != ConnectionMode.DISCONNECTED:
            conn._connecting = False
            return

        # 릴레이 즉시 연결
        if self._relay_ws:
            conn.ws = self._relay_ws
            conn.mode = ConnectionMode.RELAY
            conn._connecting = False
            logger.info(f"[P2P] {agent_id} ★ 릴레이 즉시 연결 "
                        f"(백그라운드 P2P 업그레이드 5초 후 시작)")
            self.agent_connected.emit(agent_id, conn.ip_public or "relay")
            self.connection_mode_changed.emit(agent_id, "relay")
            # 에이전트에 시스템 정보 요청 (DB 없이도 정보 표시 가능)
            self._send_to_agent(agent_id, {'type': 'request_info'})
            # 백그라운드 P2P 업그레이드 (5초 후 시작, 주기적 재시도)
            asyncio.ensure_future(self._delayed_p2p_upgrade(agent_id, delay=5))
            return

        # ── 2단계: 릴레이 불가 시 WAN 직접 시도 (폴백) ──
        if conn.ip_public:
            logger.info(f"[P2P] {agent_id} 릴레이 불가 → WAN 직접 시도: "
                        f"{conn.ip_public}:{conn.ws_port}")
            result = await self._try_p2p_connect(
                f"ws://{conn.ip_public}:{conn.ws_port}",
                timeout=self._timeout_wan,
            )
            if result:
                ws, auth_info = result
                conn.ws = ws
                conn.mode = ConnectionMode.WAN
                conn.info = auth_info
                conn._connecting = False
                logger.info(f"[P2P] {agent_id} ★ WAN 연결 성공: "
                            f"{conn.ip_public}:{conn.ws_port}")
                self.agent_connected.emit(agent_id, conn.ip_public)
                self.connection_mode_changed.emit(agent_id, "wan")
                conn._recv_task = asyncio.create_task(self._recv_loop(agent_id))
                return

        # 전부 실패
        conn.mode = ConnectionMode.DISCONNECTED
        conn._connecting = False
        logger.warning(f"[P2P] {agent_id} 연결 실패 (릴레이/WAN 모두 불가)")
        self.agent_disconnected.emit(agent_id)

    # P2P 업그레이드 실패 후 재시도 대기 시간 (초)
    _UPGRADE_COOLDOWN = 30
    # 릴레이 상태에서 WAN 업그레이드 주기적 시도 간격 (초)
    _UPGRADE_RETRY_INTERVAL = 60

    async def _delayed_p2p_upgrade(self, agent_id: str, delay: float = 30):
        """릴레이 연결 후 지연 P2P 업그레이드 시도 + 주기적 재시도"""
        try:
            await asyncio.sleep(delay)
            conn = self._connections.get(agent_id)
            if conn and conn.mode == ConnectionMode.RELAY and not conn._upgrading:
                logger.info(f"[P2P] {agent_id} 자동 P2P 업그레이드 시도 ({delay}초 경과, UDP→WAN)")
                await self._try_p2p_upgrade(agent_id)
        except asyncio.CancelledError:
            return

        # 릴레이 상태 유지 시 주기적으로 WAN 업그레이드 재시도
        while True:
            try:
                await asyncio.sleep(self._UPGRADE_RETRY_INTERVAL)
                conn = self._connections.get(agent_id)
                if not conn or conn.mode != ConnectionMode.RELAY:
                    break  # 연결 해제 또는 이미 업그레이드됨
                if not conn._upgrading:
                    logger.info(f"[P2P] {agent_id} 주기적 P2P 업그레이드 시도 (UDP→WAN)")
                    await self._try_p2p_upgrade(agent_id)
            except asyncio.CancelledError:
                return

    async def _try_p2p_upgrade(self, agent_id: str):
        """릴레이 → P2P 직접 연결 업그레이드 (UDP 우선, WAN TCP 보조)

        UDP 홀펀칭은 포트포워딩 불필요 → 우선 시도.
        WAN TCP는 포트포워딩 필요 → 보조적으로 시도.
        """
        conn = self._connections.get(agent_id)
        if not conn or conn.mode != ConnectionMode.RELAY:
            return

        # 쿨다운: 최근 실패 후 일정 시간 내 재시도 방지
        import time as _time
        now = _time.time()
        if conn._last_upgrade_fail and (now - conn._last_upgrade_fail) < self._UPGRADE_COOLDOWN:
            remaining = int(self._UPGRADE_COOLDOWN - (now - conn._last_upgrade_fail))
            logger.debug(f"[P2P] {agent_id} 업그레이드 쿨다운 ({remaining}초 남음)")
            return

        conn._upgrading = True
        try:
            # 1단계: UDP 홀펀칭 (포트포워딩 불필요 — 우선!)
            if self._relay_ws:
                logger.info(f"[P2P] {agent_id} P2P 업그레이드: UDP 홀펀칭 시도")
                udp_ch = await self._try_udp_punch(agent_id)
                if udp_ch:
                    conn.udp_channel = udp_ch
                    conn.mode = ConnectionMode.UDP_P2P
                    logger.info(f"[P2P] {agent_id} ★ 릴레이→UDP P2P 업그레이드 성공!")
                    # 시그널 전에 recv/ping 루프 시작
                    udp_ch.start(
                        on_control=lambda msg, aid=agent_id: self._on_udp_control(aid, msg),
                        on_video=lambda t, d, aid=agent_id: self._on_udp_video(aid, t, d),
                    )
                    self.connection_mode_changed.emit(agent_id, "udp_p2p")
                    # 에이전트에 thumbnail push 재시작 (UDP 채널 경유)
                    self._send_to_agent(agent_id, {
                        'type': 'stop_thumbnail_push'
                    })
                    self._send_to_agent(agent_id, {
                        'type': 'start_thumbnail_push', 'interval': 1.0
                    })
                    return

            # 2단계: WAN TCP (포트포워딩 필요 — 보조)
            if conn.ip_public:
                logger.info(f"[P2P] {agent_id} P2P 업그레이드: WAN TCP 시도 "
                            f"{conn.ip_public}:{conn.ws_port}")
                result = await self._try_p2p_connect(
                    f"ws://{conn.ip_public}:{conn.ws_port}",
                    timeout=self._timeout_wan,
                )
                if result:
                    ws, auth_info = result
                    conn.ws = ws
                    conn.mode = ConnectionMode.WAN
                    conn.info = auth_info
                    logger.info(f"[P2P] {agent_id} ★ 릴레이→WAN 업그레이드: "
                                f"{conn.ip_public}:{conn.ws_port}")
                    self.connection_mode_changed.emit(agent_id, "wan")
                    conn._recv_task = asyncio.create_task(self._recv_loop(agent_id))
                    return

            # P2P 실패 — 릴레이 유지, 쿨다운 시작
            conn._last_upgrade_fail = _time.time()
            logger.info(f"[P2P] {agent_id} P2P 업그레이드 실패 — "
                         f"{self._UPGRADE_COOLDOWN}초 후 재시도 "
                         f"(ip={conn.ip_public or 'N/A'}:{conn.ws_port})")
        finally:
            conn._upgrading = False

    async def _try_p2p_connect(self, url: str, timeout: int = 3):
        """P2P 직접 WS 연결 시도 + 인증 핸드셰이크

        Returns:
            (ws, auth_info) 또는 None. auth_info는 auth_ok 메시지 dict.
        """
        try:
            ws = await asyncio.wait_for(
                websockets.connect(
                    url,
                    max_size=50 * 1024 * 1024,
                    ping_interval=20,
                    ping_timeout=20,
                ),
                timeout=timeout,
            )

            # P2P 인증 핸드셰이크
            await ws.send(json.dumps({
                'type': 'auth',
                'token': self._token,
                'manager_id': self._manager_id or 'manager',
            }))

            raw = await asyncio.wait_for(ws.recv(), timeout=5)
            msg = json.loads(raw)
            if msg.get('type') == 'auth_ok':
                return ws, msg
            else:
                await ws.close()
        except Exception as e:
            logger.info(f"[P2P] 연결 실패 ({url}): {type(e).__name__}: {e}")
        return None

    async def _close_connection(self, agent_id: str):
        """에이전트 연결 닫기"""
        conn = self._connections.get(agent_id)
        if not conn:
            return

        if conn._recv_task and not conn._recv_task.done():
            conn._recv_task.cancel()

        # UDP 채널 종료
        if conn.udp_channel:
            try:
                await conn.udp_channel.close()
            except Exception:
                pass
            conn.udp_channel = None

        if conn.ws and conn.mode != ConnectionMode.RELAY:
            try:
                await conn.ws.close()
            except Exception:
                pass

        conn.ws = None
        conn.mode = ConnectionMode.DISCONNECTED

    # ==================== P2P 수신 루프 ====================

    async def _recv_loop(self, agent_id: str):
        """P2P 직접 연결의 메시지 수신 루프"""
        conn = self._connections.get(agent_id)
        if not conn or not conn.ws:
            return

        try:
            async for message in conn.ws:
                if self._stop_event.is_set():
                    break
                if isinstance(message, str):
                    self._handle_p2p_text(agent_id, message)
                elif isinstance(message, bytes):
                    self._handle_p2p_binary(agent_id, message)
        except websockets.exceptions.ConnectionClosed:
            logger.info(f"[P2P] {agent_id} 연결 종료")
        except asyncio.CancelledError:
            return
        except Exception as e:
            logger.warning(f"[P2P] {agent_id} 수신 오류: {e}")
        finally:
            conn.ws = None
            conn.mode = ConnectionMode.DISCONNECTED
            self.agent_disconnected.emit(agent_id)

            # 자동 재연결
            if not self._stop_event.is_set():
                logger.info(f"[P2P] {agent_id} {self._reconnect_interval}초 후 재연결...")
                await asyncio.sleep(self._reconnect_interval)
                if not self._stop_event.is_set():
                    await self._connect_cascade(agent_id)

    def _handle_p2p_text(self, agent_id: str, raw: str):
        """P2P 직접 연결 JSON 처리 (agent_id prefix 불필요 — 직접 연결)"""
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return

        msg_type = msg.get('type', '')

        if msg_type == 'stream_started':
            info = {
                'codec': msg.get('codec', 'mjpeg'),
                'encoder': msg.get('encoder', ''),
                'width': msg.get('width', 0),
                'height': msg.get('height', 0),
                'fps': msg.get('fps', 15),
                'quality': msg.get('quality', 60),
            }
            self.stream_started.emit(agent_id, info)
            logger.info(f"[P2P] {agent_id} 스트림 시작: codec={info['codec']}")

        elif msg_type == 'clipboard':
            fmt = msg.get('format', '')
            data = msg.get('data', '')
            if fmt == 'text':
                self.clipboard_received.emit(agent_id, 'text', data)
            elif fmt == 'image':
                png_data = base64.b64decode(data)
                self.clipboard_received.emit(agent_id, 'image', png_data)

        elif msg_type == 'pong':
            import time
            send_time = self._ping_times.pop(agent_id, None)
            if send_time is not None:
                rtt_ms = int((time.time() - send_time) * 1000)
                self.latency_measured.emit(agent_id, rtt_ms)

        elif msg_type == 'file_progress':
            received = msg.get('received', 0)
            total = msg.get('total', 0)
            self.file_progress.emit(agent_id, received, total)

        elif msg_type == 'file_complete':
            remote_path = msg.get('path', '')
            self.file_complete.emit(agent_id, remote_path)

        elif msg_type == 'execute_result':
            command = msg.get('command', '')
            stdout = msg.get('stdout', '')
            stderr = msg.get('stderr', '')
            returncode = msg.get('returncode', -1)
            output = stdout if stdout else stderr
            self.command_result.emit(agent_id, command, output, returncode)

        elif msg_type == 'update_status':
            self.update_status_received.emit(agent_id, msg)

        elif msg_type == 'monitors_info':
            monitors = msg.get('monitors', [])
            self.monitors_received.emit(agent_id, monitors)

        elif msg_type == 'performance_data':
            self.performance_received.emit(agent_id, msg)

        elif msg_type == 'power_result':
            pass  # 전원 명령 결과 (로그용)

    def _handle_p2p_binary(self, agent_id: str, data: bytes):
        """P2P 직접 연결 바이너리 처리 (32B prefix 없음 — 직접 연결)"""
        if len(data) < 2:
            return

        header = data[0]
        frame_data = data[1:]

        if header == self.HEADER_THUMBNAIL:
            self.thumbnail_received.emit(agent_id, frame_data)
        elif header == self.HEADER_STREAM:
            self.screen_frame_received.emit(agent_id, frame_data)
        elif header in (self.HEADER_H264_KEYFRAME, self.HEADER_H264_DELTA):
            self.h264_frame_received.emit(agent_id, header, frame_data)
        elif header == self.HEADER_AUDIO:
            self.audio_received.emit(agent_id, frame_data)

    # ==================== 서버 릴레이 (폴백) ====================

    async def _relay_connect_loop(self):
        """서버 WS 릴레이에 접속 (폴백용, 자동 재연결 — 지수 백오프)"""
        if not self._server_url or not self._token:
            return

        base = self._server_url
        if base.startswith('https://'):
            base = 'wss://' + base[8:]
        elif base.startswith('http://'):
            base = 'ws://' + base[7:]
        elif not base.startswith(('ws://', 'wss://')):
            base = 'ws://' + base
        ws_url = f"{base}/ws/manager?token={self._token}"

        retry_delay = 1  # 초기 1초, 최대 60초까지 지수 백오프
        MAX_RETRY_DELAY = 60

        while not self._stop_event.is_set():
            try:
                logger.info("[P2P/Relay] 서버 릴레이 접속 시도...")
                async with websockets.connect(
                    ws_url,
                    max_size=50 * 1024 * 1024,
                    ping_interval=20,
                    ping_timeout=20,
                    close_timeout=10,
                ) as ws:
                    self._relay_ws = ws
                    logger.info("[P2P/Relay] 서버 릴레이 접속 성공 (폴백 대기)")
                    retry_delay = 1  # 연결 성공 시 백오프 리셋

                    async for message in ws:
                        if self._stop_event.is_set():
                            break
                        if isinstance(message, str):
                            self._handle_relay_text(message)
                        elif isinstance(message, bytes):
                            self._handle_relay_binary(message)

            except websockets.exceptions.ConnectionClosed:
                logger.info("[P2P/Relay] 서버 연결 종료")
            except asyncio.CancelledError:
                return
            except Exception as e:
                if not self._stop_event.is_set():
                    logger.debug(f"[P2P/Relay] 서버 연결 오류: {type(e).__name__}: {e}")
            finally:
                self._relay_ws = None
                # 릴레이 모드인 에이전트들 연결 해제
                for agent_id, conn in list(self._connections.items()):
                    if conn.mode == ConnectionMode.RELAY:
                        conn.ws = None
                        conn.mode = ConnectionMode.DISCONNECTED
                        self.agent_disconnected.emit(agent_id)

            if not self._stop_event.is_set():
                logger.debug(f"[P2P/Relay] {retry_delay}초 후 재연결...")
                # 0.1초 단위로 대기 (종료 이벤트 빠른 감지)
                for _ in range(int(retry_delay * 10)):
                    if self._stop_event.is_set():
                        return
                    await asyncio.sleep(0.1)
                retry_delay = min(retry_delay * 2, MAX_RETRY_DELAY)

    def _handle_relay_text(self, raw: str):
        """서버 릴레이 JSON 처리 (기존 v2.x 프로토콜)"""
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return

        msg_type = msg.get('type', '')
        source_agent = msg.get('source_agent', '')

        # 에이전트 연결/해제 (서버가 전달)
        if msg_type == 'auth':
            agent_id = msg.get('agent_id', source_agent)
            if agent_id:
                conn = self._connections.get(agent_id)
                if not conn:
                    conn = AgentConnection(agent_id=agent_id)
                    self._connections[agent_id] = conn
                # cascade 진행 중이면 info만 업데이트
                conn.info = {
                    'hostname': msg.get('hostname', ''),
                    'os_info': msg.get('os_info', ''),
                    'ip': msg.get('ip', ''),
                    'screen_width': msg.get('screen_width', 1920),
                    'screen_height': msg.get('screen_height', 1080),
                    'agent_version': msg.get('agent_version', ''),
                }
                if not conn._connecting and conn.mode == ConnectionMode.DISCONNECTED:
                    conn.ws = self._relay_ws
                    conn.mode = ConnectionMode.RELAY
                    self.agent_connected.emit(agent_id, msg.get('ip', ''))
                    self.connection_mode_changed.emit(agent_id, "relay")
                    # 에이전트에 시스템 정보 요청 (하드웨어 등)
                    self._send_to_agent(agent_id, {'type': 'request_info'})
                    logger.info(f"[P2P/Relay] 에이전트 연결: {agent_id} (릴레이)")
            return

        if msg_type == 'agent_connected':
            agent_id = source_agent
            real_ip = msg.get('real_ip', '')
            ws_port = msg.get('ws_port', 21350)
            if agent_id:
                conn = self._connections.get(agent_id)
                if not conn:
                    conn = AgentConnection(agent_id=agent_id)
                    self._connections[agent_id] = conn

                # IP 정보 업데이트 (어떤 상태든)
                if real_ip:
                    conn.ip_public = real_ip
                if ws_port:
                    conn.ws_port = ws_port

                if conn._connecting:
                    # _connect_cascade 실행 중 — IP만 업데이트 (cascade가 대기 후 WAN 재시도함)
                    logger.info(f"[P2P/Relay] {agent_id} 공인IP 수신 (cascade 대기 중 → WAN 재시도 예정): "
                                f"real_ip={real_ip or 'N/A'}, ws_port={ws_port}")
                elif conn.mode == ConnectionMode.DISCONNECTED:
                    # 새 에이전트 — 릴레이 설정 + WAN 업그레이드 자동 시도
                    logger.info(f"[P2P/Relay] 에이전트 연결: {agent_id} "
                                f"(real_ip={real_ip or 'N/A'}, ws_port={ws_port})")
                    conn.ws = self._relay_ws
                    conn.mode = ConnectionMode.RELAY
                    self.agent_connected.emit(agent_id, real_ip or '')
                    self.connection_mode_changed.emit(agent_id, "relay")
                    # 에이전트에 시스템 정보 요청
                    self._send_to_agent(agent_id, {'type': 'request_info'})
                    if real_ip and not conn._upgrading:
                        # 즉시 WAN 업그레이드 + 실패 시 주기적 재시도
                        asyncio.ensure_future(self._delayed_p2p_upgrade(agent_id, delay=3))
                elif conn.mode == ConnectionMode.RELAY and real_ip:
                    # 이미 릴레이 연결인데 real_ip가 새로 왔으면 업그레이드 시도
                    if not conn._upgrading:
                        asyncio.ensure_future(self._try_p2p_upgrade(agent_id))
            return

        if msg_type == 'agent_disconnected':
            agent_id = source_agent
            if agent_id:
                conn = self._connections.get(agent_id)
                if conn and conn.mode == ConnectionMode.RELAY:
                    conn.ws = None
                    conn.mode = ConnectionMode.DISCONNECTED
                    self.agent_disconnected.emit(agent_id)
                    logger.info(f"[P2P/Relay] 에이전트 해제: {agent_id}")
            return

        # UDP 홀펀칭 시그널링: udp_answer (에이전트→매니저)
        if msg_type == 'udp_answer':
            from .udp_punch import handle_udp_answer
            handle_udp_answer(msg)
            return

        # 에이전트→매니저 메시지 (릴레이 경유)
        agent_id = source_agent
        if not agent_id:
            return

        if msg_type == 'stream_started':
            info = {
                'codec': msg.get('codec', 'mjpeg'),
                'encoder': msg.get('encoder', ''),
                'width': msg.get('width', 0),
                'height': msg.get('height', 0),
                'fps': msg.get('fps', 15),
                'quality': msg.get('quality', 60),
            }
            self.stream_started.emit(agent_id, info)
        elif msg_type == 'clipboard':
            fmt = msg.get('format', '')
            data = msg.get('data', '')
            if fmt == 'text':
                self.clipboard_received.emit(agent_id, 'text', data)
            elif fmt == 'image':
                png_data = base64.b64decode(data)
                self.clipboard_received.emit(agent_id, 'image', png_data)
        elif msg_type == 'file_progress':
            self.file_progress.emit(agent_id, msg.get('received', 0), msg.get('total', 0))
        elif msg_type == 'file_complete':
            self.file_complete.emit(agent_id, msg.get('path', ''))
        elif msg_type == 'execute_result':
            command = msg.get('command', '')
            stdout = msg.get('stdout', '')
            stderr = msg.get('stderr', '')
            returncode = msg.get('returncode', -1)
            self.command_result.emit(agent_id, command, stdout if stdout else stderr, returncode)
        elif msg_type == 'system_info':
            # 에이전트가 보낸 시스템 정보 (릴레이 경유)
            conn = self._connections.get(agent_id)
            if conn:
                info_data = {
                    'hostname': msg.get('hostname', ''),
                    'os_info': msg.get('os_info', ''),
                    'ip': msg.get('ip', ''),
                    'ip_public': msg.get('ip_public', ''),
                    'mac_address': msg.get('mac_address', ''),
                    'screen_width': msg.get('screen_width', 0),
                    'screen_height': msg.get('screen_height', 0),
                    'agent_version': msg.get('agent_version', ''),
                    'cpu_model': msg.get('cpu_model', ''),
                    'cpu_cores': msg.get('cpu_cores', 0),
                    'ram_gb': msg.get('ram_gb', 0.0),
                    'motherboard': msg.get('motherboard', ''),
                    'gpu_model': msg.get('gpu_model', ''),
                }
                conn.info.update(info_data)
                if msg.get('ip_public'):
                    conn.ip_public = msg['ip_public']
                logger.info(f"[P2P/Relay] {agent_id} system_info 수신: "
                            f"hostname={info_data['hostname']}, "
                            f"ip_public={info_data['ip_public'] or 'N/A'}, "
                            f"version={info_data['agent_version']}")
                self.agent_info_received.emit(agent_id, info_data)
        elif msg_type == 'update_status':
            self.update_status_received.emit(agent_id, msg)

    def _handle_relay_binary(self, data: bytes):
        """서버 릴레이 바이너리 처리 (32B prefix 있음 — 기존 방식)"""
        if len(data) < AGENT_ID_LEN + 2:
            return

        agent_id = _unpad_agent_id(data[:AGENT_ID_LEN])
        payload = data[AGENT_ID_LEN:]

        header = payload[0]
        frame_data = payload[1:]

        if header == self.HEADER_THUMBNAIL:
            self.thumbnail_received.emit(agent_id, frame_data)
        elif header == self.HEADER_STREAM:
            self.screen_frame_received.emit(agent_id, frame_data)
        elif header in (self.HEADER_H264_KEYFRAME, self.HEADER_H264_DELTA):
            self.h264_frame_received.emit(agent_id, header, frame_data)
        elif header == self.HEADER_AUDIO:
            self.audio_received.emit(agent_id, frame_data)

    # ==================== UDP 홀펀칭 P2P ====================

    async def _try_udp_punch(self, agent_id: str):
        """UDP 홀펀칭 시도 → UdpChannel 또는 None

        릴레이 WS를 통해 시그널링하여 NAT 뒤의 에이전트와 직접 UDP 연결.
        """
        if not self._relay_ws:
            return None

        try:
            from .udp_punch import punch_as_manager
            logger.info(f"[UDP-Punch] {agent_id} 홀펀칭 시도...")
            channel = await punch_as_manager(
                self._relay_ws, agent_id, timeout=10.0
            )
            return channel
        except Exception as e:
            logger.info(f"[UDP-Punch] {agent_id} 홀펀칭 실패: {e}")
            return None

    def _on_udp_control(self, agent_id: str, msg: dict):
        """UDP 채널로 수신된 제어 메시지 처리"""
        msg_type = msg.get('type', '')

        if msg_type == 'stream_started':
            info = {
                'codec': msg.get('codec', 'mjpeg'),
                'encoder': msg.get('encoder', ''),
                'width': msg.get('width', 0),
                'height': msg.get('height', 0),
                'fps': msg.get('fps', 15),
                'quality': msg.get('quality', 60),
            }
            self.stream_started.emit(agent_id, info)
            logger.info(f"[UDP] {agent_id} 스트림 시작: codec={info['codec']}")
        elif msg_type == 'clipboard':
            fmt = msg.get('format', '')
            data = msg.get('data', '')
            if fmt == 'text':
                self.clipboard_received.emit(agent_id, 'text', data)
            elif fmt == 'image':
                png_data = base64.b64decode(data)
                self.clipboard_received.emit(agent_id, 'image', png_data)
        elif msg_type == 'file_progress':
            self.file_progress.emit(agent_id, msg.get('received', 0), msg.get('total', 0))
        elif msg_type == 'file_complete':
            self.file_complete.emit(agent_id, msg.get('path', ''))
        elif msg_type == 'execute_result':
            command = msg.get('command', '')
            stdout = msg.get('stdout', '')
            stderr = msg.get('stderr', '')
            returncode = msg.get('returncode', -1)
            self.command_result.emit(agent_id, command, stdout if stdout else stderr, returncode)
        elif msg_type == 'update_status':
            self.update_status_received.emit(agent_id, msg)

    def _on_udp_video(self, agent_id: str, frame_type: int, data: bytes):
        """UDP 채널로 수신된 비디오 프레임 처리"""
        from .udp_channel import TYPE_THUMBNAIL, TYPE_STREAM, TYPE_H264_KEY, TYPE_H264_DELTA

        if frame_type == TYPE_THUMBNAIL:
            self.thumbnail_received.emit(agent_id, data)
        elif frame_type == TYPE_STREAM:
            self.screen_frame_received.emit(agent_id, data)
        elif frame_type == TYPE_H264_KEY:
            self.h264_frame_received.emit(agent_id, self.HEADER_H264_KEYFRAME, data)
        elif frame_type == TYPE_H264_DELTA:
            self.h264_frame_received.emit(agent_id, self.HEADER_H264_DELTA, data)
