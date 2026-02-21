"""다중 에이전트 WebSocket 릴레이 클라이언트 (매니저 측)

매니저가 서버(log.wellcomll.org:4797)에 WS 클라이언트로 접속하면,
서버가 같은 계정의 에이전트들과 매니저 사이의 메시지를 릴레이한다.
포트포워딩 불필요.

아키텍처:
  매니저(이 코드) = WS 클라이언트 (서버/ws/manager?token=JWT 에 접속)
  에이전트 = WS 클라이언트 (서버/ws/agent?token=JWT 에 접속)
  서버 = REST API + WS 릴레이 (양쪽 메시지를 중계)

메시지 프로토콜 (서버 릴레이 경유):
  매니저→서버→에이전트:
    JSON: {"type": "...", "target_agent": "DESKTOP-ABC", ...}
    Binary: agent_id(32바이트) + 원본 데이터
  에이전트→서버→매니저:
    JSON: {"type": "...", "source_agent": "DESKTOP-ABC", ...}
    Binary: agent_id(32바이트) + 원본 데이터 (0x01/0x02 + JPEG)
"""

import asyncio
import json
import base64
import logging
import threading
import os
from typing import Dict, List, Optional

from PyQt6.QtCore import QObject, pyqtSignal

logger = logging.getLogger(__name__)

try:
    import websockets
    WEBSOCKETS_AVAILABLE = True
except ImportError:
    WEBSOCKETS_AVAILABLE = False
    logger.warning("websockets 미설치 — 에이전트 연결 기능 비활성화")

# agent_id 패딩 길이 (서버와 동일)
AGENT_ID_LEN = 32


def _pad_agent_id(agent_id: str) -> bytes:
    """agent_id를 32바이트로 패딩"""
    return agent_id.encode('utf-8')[:AGENT_ID_LEN].ljust(AGENT_ID_LEN, b'\x00')


def _unpad_agent_id(data: bytes) -> str:
    """32바이트에서 agent_id 추출"""
    return data[:AGENT_ID_LEN].rstrip(b'\x00').decode('utf-8', errors='replace')


class AgentServer(QObject):
    """매니저 측 WebSocket 릴레이 클라이언트

    서버(log.wellcomll.org:4797/ws/manager)에 WS 접속하여,
    서버가 중계하는 에이전트 메시지를 수신/전송한다.
    기존 시그널/명령 인터페이스 100% 유지.
    """

    # 시그널 (UI/PCManager에서 사용)
    agent_connected = pyqtSignal(str, str)         # agent_id, agent_ip
    agent_disconnected = pyqtSignal(str)            # agent_id
    thumbnail_received = pyqtSignal(str, bytes)     # agent_id, jpeg_data
    screen_frame_received = pyqtSignal(str, bytes)  # agent_id, jpeg_data
    clipboard_received = pyqtSignal(str, str, object)  # agent_id, format, data
    file_progress = pyqtSignal(str, int, int)       # agent_id, sent, total
    file_complete = pyqtSignal(str, str)            # agent_id, remote_path
    command_result = pyqtSignal(str, str, str, int)  # agent_id, command, output, returncode

    CHUNK_SIZE = 64 * 1024  # 64KB

    # 바이너리 프레임 헤더
    HEADER_THUMBNAIL = 0x01
    HEADER_STREAM = 0x02

    def __init__(self):
        super().__init__()
        self._ws: Optional[object] = None              # 서버와의 websocket
        self._server_url: str = ''                      # ws://서버/ws/manager?token=JWT
        self._token: str = ''                           # JWT 토큰
        self._connected_agents: set = set()             # 현재 연결된 agent_id 집합
        self._agent_info: Dict[str, dict] = {}          # agent_id → {hostname, os_info, ip, ...}
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    @property
    def connected_count(self) -> int:
        return len(self._connected_agents)

    def get_connected_agents(self) -> List[str]:
        return list(self._connected_agents)

    def is_agent_connected(self, agent_id: str) -> bool:
        return agent_id in self._connected_agents

    def get_agent_info(self, agent_id: str) -> Optional[dict]:
        return self._agent_info.get(agent_id)

    # ==================== 수명주기 ====================

    def start_connection(self, server_url: str, token: str):
        """서버에 WS 클라이언트로 접속 시작

        Args:
            server_url: REST API 기본 URL (예: http://log.wellcomll.org:4797)
            token: JWT 토큰
        """
        if not WEBSOCKETS_AVAILABLE:
            logger.error("[AgentServer] websockets 미설치 — 접속 불가")
            return

        if self._thread and self._thread.is_alive():
            logger.info("[AgentServer] 이미 접속 스레드 실행 중 — 스킵")
            return

        # http(s) → ws(s) 변환
        ws_url = server_url.replace('https://', 'wss://').replace('http://', 'ws://')
        self._server_url = f"{ws_url}/ws/manager?token={token}"
        self._token = token

        # 토큰 일부만 로그 (보안)
        token_preview = token[:20] + "..." if len(token) > 20 else token
        logger.info(f"[AgentServer] 서버 URL: {server_url}")
        logger.info(f"[AgentServer] WS URL: {ws_url}/ws/manager?token={token_preview}")

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop, daemon=True, name='AgentWSClient'
        )
        self._thread.start()
        logger.info(f"[AgentServer] 서버 WS 접속 스레드 시작")

    def stop_connection(self):
        """서버 WS 연결 종료"""
        self._stop_event.set()

        if self._ws and self._loop and self._loop.is_running():
            try:
                asyncio.run_coroutine_threadsafe(self._ws.close(), self._loop)
            except Exception:
                pass

        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)

        self._connected_agents.clear()
        self._agent_info.clear()

    # 하위 호환: 기존 start_server / stop_server 이름 유지
    def start_server(self):
        """하위 호환 — start_connection을 사용하세요"""
        logger.warning("[AgentServer] start_server() 호출됨 — start_connection() 사용 필요")

    def stop_server(self):
        """하위 호환 — stop_connection"""
        self.stop_connection()

    # ==================== 에이전트에 명령 전송 ====================

    def request_thumbnail(self, agent_id: str):
        """썸네일 요청"""
        self._send_to_agent(agent_id, {'type': 'request_thumbnail'})

    def start_streaming(self, agent_id: str, fps: int = 15, quality: int = 60):
        """전체 화면 스트리밍 시작"""
        self._send_to_agent(agent_id, {
            'type': 'start_stream',
            'fps': fps,
            'quality': quality,
        })

    def stop_streaming(self, agent_id: str):
        """스트리밍 중지"""
        self._send_to_agent(agent_id, {'type': 'stop_stream'})

    def send_key_event(self, agent_id: str, key: str, action: str,
                       modifiers: list = None):
        """키보드 이벤트 전송"""
        self._send_to_agent(agent_id, {
            'type': 'key_event',
            'key': key,
            'action': action,
            'modifiers': modifiers or [],
        })

    def send_mouse_event(self, agent_id: str, x: int, y: int,
                         button: str = 'none', action: str = 'move',
                         scroll_delta: int = 0):
        """마우스 이벤트 전송"""
        self._send_to_agent(agent_id, {
            'type': 'mouse_event',
            'x': x,
            'y': y,
            'button': button,
            'action': action,
            'scroll_delta': scroll_delta,
        })

    def send_clipboard_text(self, agent_id: str, text: str):
        """텍스트 클립보드 전송"""
        self._send_to_agent(agent_id, {
            'type': 'clipboard',
            'format': 'text',
            'data': text,
        })

    def send_clipboard_image(self, agent_id: str, image_data: bytes):
        """이미지 클립보드 전송 (PNG bytes)"""
        self._send_to_agent(agent_id, {
            'type': 'clipboard',
            'format': 'image',
            'data': base64.b64encode(image_data).decode('ascii'),
        })

    def send_file(self, agent_id: str, filepath: str):
        """파일 전송 (백그라운드)"""
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
        """원격 명령 실행"""
        self._send_to_agent(agent_id, {
            'type': 'execute',
            'command': command,
        })

    # ==================== 브로드캐스트 ====================

    def broadcast_key_event(self, agent_ids: List[str], key: str,
                            action: str, modifiers: list = None):
        msg_dict = {
            'type': 'key_event',
            'key': key,
            'action': action,
            'modifiers': modifiers or [],
        }
        for agent_id in agent_ids:
            if agent_id in self._connected_agents:
                self._send_to_agent(agent_id, msg_dict)

    def broadcast_mouse_event(self, agent_ids: List[str], x: int, y: int,
                              button: str = 'none', action: str = 'move',
                              scroll_delta: int = 0):
        msg_dict = {
            'type': 'mouse_event',
            'x': x, 'y': y,
            'button': button, 'action': action,
            'scroll_delta': scroll_delta,
        }
        for agent_id in agent_ids:
            if agent_id in self._connected_agents:
                self._send_to_agent(agent_id, msg_dict)

    def broadcast_file(self, agent_ids: List[str], filepath: str):
        for agent_id in agent_ids:
            self.send_file(agent_id, filepath)

    def broadcast_command(self, agent_ids: List[str], command: str):
        for agent_id in agent_ids:
            self.execute_command(agent_id, command)

    # ==================== 내부 구현 ====================

    def _send_to_agent(self, agent_id: str, msg_dict: dict):
        """특정 에이전트에 JSON 메시지 전송 (서버 릴레이 경유)

        target_agent 필드를 추가하여 서버가 올바른 에이전트에 전달.
        """
        if not self._ws or not self._loop or not self._loop.is_running():
            return

        msg_dict['target_agent'] = agent_id
        msg = json.dumps(msg_dict)
        asyncio.run_coroutine_threadsafe(self._ws.send(msg), self._loop)

    def _send_binary_to_agent(self, agent_id: str, data: bytes):
        """특정 에이전트에 바이너리 전송 (서버 릴레이 경유)

        agent_id(32바이트) 프리픽스를 추가하여 서버가 올바른 에이전트에 전달.
        """
        if not self._ws or not self._loop or not self._loop.is_running():
            return

        prefixed = _pad_agent_id(agent_id) + data
        asyncio.run_coroutine_threadsafe(self._ws.send(prefixed), self._loop)

    async def _send_file_async(self, agent_id: str, filepath: str):
        """파일 전송 코루틴 (서버 릴레이 경유)"""
        if not self._ws:
            return

        filename = os.path.basename(filepath)
        filesize = os.path.getsize(filepath)

        # file_start 전송
        self._send_to_agent(agent_id, {
            'type': 'file_start',
            'name': filename,
            'size': filesize,
        })

        # file_ack 대기 — 에이전트의 응답은 서버 릴레이를 통해 _handle_text_message로 수신됨
        # 여기서는 단순히 대기 후 전송 (실제 ack은 비동기로 처리)
        await asyncio.sleep(0.5)

        sent = 0
        with open(filepath, 'rb') as f:
            while True:
                chunk = f.read(self.CHUNK_SIZE)
                if not chunk:
                    break
                # 바이너리 청크 전송 (agent_id 프리픽스)
                self._send_binary_to_agent(agent_id, chunk)
                sent += len(chunk)
                self.file_progress.emit(agent_id, sent, filesize)

        # file_end 전송
        self._send_to_agent(agent_id, {
            'type': 'file_end',
            'name': filename,
        })

    def _run_loop(self):
        """asyncio 이벤트 루프 + WS 클라이언트 실행 (별도 스레드)"""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._connect())
        except Exception as e:
            if not self._stop_event.is_set():
                logger.error(f"AgentServer 오류: {e}")
        finally:
            try:
                self._loop.close()
            except Exception:
                pass
            self._loop = None

    async def _connect(self):
        """서버 WS에 접속 + 자동 재연결 루프"""
        reconnect_interval = 5  # 초
        connect_count = 0

        while not self._stop_event.is_set():
            connect_count += 1
            try:
                logger.info(f"[AgentServer] 서버 WS 접속 시도 (#{connect_count})...")
                async with websockets.connect(
                    self._server_url,
                    max_size=50 * 1024 * 1024,
                    ping_interval=30,
                    ping_timeout=10,
                ) as ws:
                    self._ws = ws
                    logger.info(f"[AgentServer] 서버 WS 접속 성공 (#{connect_count})")
                    logger.info(f"[AgentServer] 메시지 수신 루프 시작 — 에이전트 대기 중...")

                    msg_count = 0
                    # 메시지 수신 루프
                    async for message in ws:
                        if self._stop_event.is_set():
                            break
                        msg_count += 1
                        if isinstance(message, str):
                            # 메시지 타입 로그 (첫 200자만)
                            try:
                                preview = json.loads(message)
                                msg_type = preview.get('type', '?')
                                source = preview.get('source_agent', '') or preview.get('agent_id', '')
                                logger.info(f"[AgentServer] 수신 #{msg_count}: type={msg_type}, agent={source}")
                            except Exception:
                                logger.info(f"[AgentServer] 수신 #{msg_count}: text ({len(message)}B)")
                            self._handle_text_message(message)
                        elif isinstance(message, bytes):
                            logger.debug(f"[AgentServer] 수신 #{msg_count}: binary ({len(message)}B)")
                            self._handle_binary_message(message)

                    logger.info(f"[AgentServer] 메시지 루프 종료 (총 {msg_count}개 수신)")

            except websockets.exceptions.ConnectionClosed as e:
                logger.warning(f"[AgentServer] 서버 WS 연결 종료: code={e.code}, reason={e.reason}")
            except (ConnectionRefusedError, OSError) as e:
                logger.warning(f"[AgentServer] 서버 접속 실패: {e}")
            except Exception as e:
                if not self._stop_event.is_set():
                    logger.warning(f"[AgentServer] 서버 접속 오류: {type(e).__name__}: {e}")
            finally:
                self._ws = None
                # 모든 에이전트 연결 해제 처리
                disconnected = list(self._connected_agents)
                if disconnected:
                    logger.info(f"[AgentServer] WS 끊김 — {len(disconnected)}개 에이전트 해제: {disconnected}")
                for aid in disconnected:
                    self._connected_agents.discard(aid)
                    self.agent_disconnected.emit(aid)

            if not self._stop_event.is_set():
                logger.info(f"[AgentServer] 재접속 대기... ({reconnect_interval}초)")
                await asyncio.sleep(reconnect_interval)

    def _handle_text_message(self, raw: str):
        """서버에서 릴레이된 JSON 텍스트 메시지 처리

        모든 메시지에 source_agent 필드가 포함됨.
        """
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return

        msg_type = msg.get('type', '')
        agent_id = msg.get('source_agent', '')

        # 에이전트 연결/해제 알림 (서버가 전달)
        if msg_type == 'auth':
            # 에이전트가 서버에 접속 → auth 메시지 릴레이됨
            agent_id = msg.get('agent_id', '') or agent_id
            if agent_id:
                self._agent_info[agent_id] = {
                    'hostname': msg.get('hostname', agent_id),
                    'os_info': msg.get('os_info', ''),
                    'ip': msg.get('ip', ''),
                    'screen_width': msg.get('screen_width', 1920),
                    'screen_height': msg.get('screen_height', 1080),
                }
                self._connected_agents.add(agent_id)
                self.agent_connected.emit(agent_id, msg.get('ip', ''))
                logger.info(f"에이전트 연결 (릴레이): {agent_id}")
            return

        if msg_type == 'agent_connected':
            # 서버가 보내는 이미 접속된 에이전트 알림
            if agent_id:
                self._connected_agents.add(agent_id)
                self.agent_connected.emit(agent_id, '')
                logger.info(f"에이전트 연결 (기존): {agent_id}")
            return

        if msg_type == 'agent_disconnected':
            if agent_id:
                self._connected_agents.discard(agent_id)
                self._agent_info.pop(agent_id, None)
                self.agent_disconnected.emit(agent_id)
                logger.info(f"에이전트 해제 (릴레이): {agent_id}")
            return

        # 에이전트로부터의 일반 메시지
        if not agent_id:
            logger.debug(f"[AgentServer] source_agent 없는 메시지 무시: type={msg_type}")
            return

        if msg_type == 'clipboard':
            fmt = msg.get('format', '')
            data = msg.get('data', '')
            if fmt == 'text':
                self.clipboard_received.emit(agent_id, 'text', data)
            elif fmt == 'image':
                png_data = base64.b64decode(data)
                self.clipboard_received.emit(agent_id, 'image', png_data)

        elif msg_type == 'pong':
            pass

        elif msg_type == 'file_progress':
            received = msg.get('received', 0)
            total = msg.get('total', 0)
            self.file_progress.emit(agent_id, received, total)

        elif msg_type == 'file_complete':
            remote_path = msg.get('path', '')
            self.file_complete.emit(agent_id, remote_path)
            logger.info(f"파일 전송 완료 [{agent_id}]: {remote_path}")

        elif msg_type == 'execute_result':
            command = msg.get('command', '')
            stdout = msg.get('stdout', '')
            stderr = msg.get('stderr', '')
            returncode = msg.get('returncode', -1)
            output = stdout if stdout else stderr
            self.command_result.emit(agent_id, command, output, returncode)

        elif msg_type == 'file_ack':
            # 파일 수신 준비 완료 — 로그만
            status = msg.get('status', '')
            logger.debug(f"파일 ack [{agent_id}]: {status}")

    def _handle_binary_message(self, data: bytes):
        """서버에서 릴레이된 바이너리 프레임 처리

        포맷: agent_id(32바이트) + 원본 바이너리(header + payload)
        """
        if len(data) < AGENT_ID_LEN + 2:
            return

        agent_id = _unpad_agent_id(data[:AGENT_ID_LEN])
        original = data[AGENT_ID_LEN:]

        header = original[0]
        payload = original[1:]

        if header == self.HEADER_THUMBNAIL:
            self.thumbnail_received.emit(agent_id, payload)
        elif header == self.HEADER_STREAM:
            self.screen_frame_received.emit(agent_id, payload)
