"""다중 에이전트 WebSocket 서버

관리PC에서 WebSocket 서버를 실행하고, 다수의 대상PC 에이전트가 연결해옴.
역방향 연결: 대상PC(클라이언트) → 관리PC(서버)
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
    logger.warning("websockets 미설치 — 에이전트 기능 비활성화")


class AgentServer(QObject):
    """다중 에이전트 WebSocket 서버 (관리PC 측)"""

    # 시그널
    agent_connected = pyqtSignal(str, str)         # agent_id, remote_ip
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

    def __init__(self, port: int = 9877):
        super().__init__()
        self._port = port
        self._agents: Dict[str, object] = {}  # agent_id → websocket
        self._agent_info: Dict[str, dict] = {}  # agent_id → {hostname, os_info, ...}
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._server = None

    @property
    def connected_count(self) -> int:
        return len(self._agents)

    def get_connected_agents(self) -> List[str]:
        return list(self._agents.keys())

    def is_agent_connected(self, agent_id: str) -> bool:
        return agent_id in self._agents

    def get_agent_info(self, agent_id: str) -> Optional[dict]:
        return self._agent_info.get(agent_id)

    # ==================== 서버 수명주기 ====================

    def start_server(self):
        """WebSocket 서버 시작 (백그라운드 스레드)"""
        if not WEBSOCKETS_AVAILABLE:
            return

        if self._thread and self._thread.is_alive():
            return

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop, daemon=True, name='AgentServer'
        )
        self._thread.start()
        logger.info(f"[AgentServer] 시작: 포트 {self._port}")

    def stop_server(self):
        """서버 중지"""
        self._stop_event.set()

        # 모든 에이전트 연결 종료
        for agent_id, ws in list(self._agents.items()):
            try:
                if self._loop and self._loop.is_running():
                    asyncio.run_coroutine_threadsafe(ws.close(), self._loop)
            except Exception:
                pass

        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)

        self._agents.clear()
        self._agent_info.clear()

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
        msg = json.dumps({
            'type': 'key_event',
            'key': key,
            'action': action,
            'modifiers': modifiers or [],
        })
        for agent_id in agent_ids:
            ws = self._agents.get(agent_id)
            if ws and self._loop and self._loop.is_running():
                asyncio.run_coroutine_threadsafe(ws.send(msg), self._loop)

    def broadcast_mouse_event(self, agent_ids: List[str], x: int, y: int,
                              button: str = 'none', action: str = 'move',
                              scroll_delta: int = 0):
        msg = json.dumps({
            'type': 'mouse_event',
            'x': x, 'y': y,
            'button': button, 'action': action,
            'scroll_delta': scroll_delta,
        })
        for agent_id in agent_ids:
            ws = self._agents.get(agent_id)
            if ws and self._loop and self._loop.is_running():
                asyncio.run_coroutine_threadsafe(ws.send(msg), self._loop)

    def broadcast_file(self, agent_ids: List[str], filepath: str):
        for agent_id in agent_ids:
            self.send_file(agent_id, filepath)

    def broadcast_command(self, agent_ids: List[str], command: str):
        for agent_id in agent_ids:
            self.execute_command(agent_id, command)

    # ==================== 내부 구현 ====================

    def _send_to_agent(self, agent_id: str, msg_dict: dict):
        """특정 에이전트에 JSON 메시지 전송"""
        ws = self._agents.get(agent_id)
        if ws and self._loop and self._loop.is_running():
            msg = json.dumps(msg_dict)
            asyncio.run_coroutine_threadsafe(ws.send(msg), self._loop)

    async def _send_file_async(self, agent_id: str, filepath: str):
        """파일 전송 코루틴"""
        ws = self._agents.get(agent_id)
        if not ws:
            return

        filename = os.path.basename(filepath)
        filesize = os.path.getsize(filepath)

        await ws.send(json.dumps({
            'type': 'file_start',
            'name': filename,
            'size': filesize,
        }))

        resp = await ws.recv()
        ack = json.loads(resp)
        if ack.get('type') != 'file_ack' or ack.get('status') != 'ready':
            logger.error(f"파일 전송 거부 [{agent_id}]: {ack}")
            return

        sent = 0
        with open(filepath, 'rb') as f:
            while True:
                chunk = f.read(self.CHUNK_SIZE)
                if not chunk:
                    break
                await ws.send(chunk)
                sent += len(chunk)
                self.file_progress.emit(agent_id, sent, filesize)

        await ws.send(json.dumps({
            'type': 'file_end',
            'name': filename,
        }))

        resp = await ws.recv()
        result = json.loads(resp)
        if result.get('type') == 'file_complete':
            remote_path = result.get('path', '')
            self.file_complete.emit(agent_id, remote_path)
            logger.info(f"파일 전송 완료 [{agent_id}]: {filename} → {remote_path}")

    def _run_loop(self):
        """asyncio 이벤트 루프 실행 (스레드)"""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._serve())
        except Exception as e:
            if not self._stop_event.is_set():
                logger.error(f"AgentServer 오류: {e}")
        finally:
            try:
                self._loop.close()
            except Exception:
                pass
            self._loop = None

    async def _serve(self):
        """WebSocket 서버 실행"""
        try:
            self._server = await websockets.serve(
                self._handle_agent,
                '0.0.0.0',
                self._port,
                max_size=50 * 1024 * 1024,
                ping_interval=30,
                ping_timeout=10,
            )
            logger.info(f"[AgentServer] 리스닝: ws://0.0.0.0:{self._port}")

            while not self._stop_event.is_set():
                await asyncio.sleep(0.5)
        finally:
            if self._server:
                self._server.close()
                await self._server.wait_closed()

    async def _handle_agent(self, websocket):
        """에이전트 연결 핸들러"""
        remote = websocket.remote_address
        remote_ip = remote[0] if remote else 'unknown'
        agent_id = None

        try:
            # 인증 대기
            raw = await asyncio.wait_for(websocket.recv(), timeout=10)
            msg = json.loads(raw)

            if msg.get('type') != 'auth':
                await websocket.close()
                return

            agent_id = msg.get('agent_id', remote_ip)
            hostname = msg.get('hostname', '')
            os_info = msg.get('os_info', '')
            screen_width = msg.get('screen_width', 1920)
            screen_height = msg.get('screen_height', 1080)

            # 인증 허용
            await websocket.send(json.dumps({'type': 'auth_ok'}))

            # 기존 같은 agent_id 연결 교체
            old_ws = self._agents.get(agent_id)
            if old_ws:
                try:
                    await old_ws.close()
                except Exception:
                    pass

            self._agents[agent_id] = websocket
            self._agent_info[agent_id] = {
                'hostname': hostname,
                'os_info': os_info,
                'ip': remote_ip,
                'screen_width': screen_width,
                'screen_height': screen_height,
            }

            self.agent_connected.emit(agent_id, remote_ip)
            logger.info(f"[AgentServer] 에이전트 연결: {agent_id} ({remote_ip})")

            # 메시지 수신 루프
            async for message in websocket:
                if self._stop_event.is_set():
                    break
                if isinstance(message, str):
                    self._handle_text_message(agent_id, message)
                elif isinstance(message, bytes):
                    self._handle_binary_message(agent_id, message)

        except websockets.exceptions.ConnectionClosed:
            logger.info(f"[AgentServer] 에이전트 연결 종료: {agent_id or remote_ip}")
        except asyncio.TimeoutError:
            logger.warning(f"[AgentServer] 인증 타임아웃: {remote_ip}")
        except Exception as e:
            logger.warning(f"[AgentServer] 핸들러 오류 [{agent_id or remote_ip}]: {e}")
        finally:
            if agent_id and self._agents.get(agent_id) is websocket:
                del self._agents[agent_id]
                self._agent_info.pop(agent_id, None)
                self.agent_disconnected.emit(agent_id)
                logger.info(f"[AgentServer] 에이전트 해제: {agent_id}")

    def _handle_text_message(self, agent_id: str, raw: str):
        """JSON 텍스트 메시지 처리"""
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return

        msg_type = msg.get('type', '')

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

        elif msg_type == 'execute_result':
            command = msg.get('command', '')
            stdout = msg.get('stdout', '')
            stderr = msg.get('stderr', '')
            returncode = msg.get('returncode', -1)
            output = stdout if stdout else stderr
            self.command_result.emit(agent_id, command, output, returncode)

    def _handle_binary_message(self, agent_id: str, data: bytes):
        """바이너리 프레임 처리 (화면 데이터)"""
        if len(data) < 2:
            return

        header = data[0]
        payload = data[1:]

        if header == self.HEADER_THUMBNAIL:
            self.thumbnail_received.emit(agent_id, payload)
        elif header == self.HEADER_STREAM:
            self.screen_frame_received.emit(agent_id, payload)
