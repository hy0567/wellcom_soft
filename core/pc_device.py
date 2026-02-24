"""단일 PC 디바이스 모델 — 에이전트가 연결된 원격 PC 하나를 표현"""

import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class PCStatus(Enum):
    ONLINE = "online"
    OFFLINE = "offline"
    CONNECTING = "connecting"
    ERROR = "error"


@dataclass
class PCInfo:
    """PC 기본 정보"""
    name: str
    agent_id: str           # 고유 식별자 (hostname 또는 UUID)
    ip: str = ""
    group: str = "default"
    os_info: str = ""       # "Windows 10 Pro 22H2"
    hostname: str = ""
    mac_address: str = ""
    screen_width: int = 1920
    screen_height: int = 1080
    memo: str = ""          # 메모 필드 (LinkIO list.json의 memo)
    public_ip: str = ""     # 공인 IP (LinkIO의 Ip1, P2P WAN용)
    ws_port: int = 21350    # 에이전트 WS 서버 포트 (P2P용)
    connection_mode: str = ""  # "lan" / "wan" / "relay" / "" (P2P 연결 모드)
    agent_version: str = ""    # 에이전트 소프트웨어 버전 (업데이터용)
    cpu_model: str = ""
    cpu_cores: int = 0
    ram_gb: float = 0.0
    motherboard: str = ""
    gpu_model: str = ""
    keymap_name: str = ""   # 지정된 키매핑 이름
    script_name: str = ""   # 지정된 스크립트 이름


class PCDevice:
    """원격 PC 디바이스"""

    def __init__(self, info: PCInfo):
        self.info = info
        self.status = PCStatus.OFFLINE
        self.last_seen: float = 0.0
        self.last_seen_str: str = ''      # 서버 last_seen (표시용)
        self.server_online: bool = False   # 서버 API가 보고한 온라인 상태
        self.last_thumbnail: Optional[bytes] = None
        self.thumbnail_time: float = 0.0
        self.is_streaming: bool = False
        self.is_controlled: bool = False
        self.agent_ws = None  # WebSocket 참조

    @property
    def name(self) -> str:
        return self.info.name

    @name.setter
    def name(self, value: str):
        self.info.name = value

    @property
    def agent_id(self) -> str:
        return self.info.agent_id

    @property
    def ip(self) -> str:
        return self.info.ip

    @property
    def group(self) -> str:
        return self.info.group

    @group.setter
    def group(self, value: str):
        self.info.group = value

    @property
    def is_online(self) -> bool:
        return self.status == PCStatus.ONLINE and self.agent_ws is not None

    def update_thumbnail(self, jpeg_data: bytes):
        """썸네일 이미지 업데이트"""
        self.last_thumbnail = jpeg_data
        self.thumbnail_time = time.time()

    def mark_online(self, ws, remote_ip: str):
        """에이전트 연결됨"""
        self.agent_ws = ws
        self.status = PCStatus.ONLINE
        self.last_seen = time.time()
        self.info.ip = remote_ip

    def mark_offline(self):
        """에이전트 연결 해제"""
        self.agent_ws = None
        self.status = PCStatus.OFFLINE
        self.is_streaming = False
        self.is_controlled = False

    def update_info(self, **kwargs):
        """PC 정보 업데이트"""
        for key, value in kwargs.items():
            if hasattr(self.info, key):
                setattr(self.info, key, value)

    def __repr__(self) -> str:
        return f"PCDevice({self.info.name}, {self.status.value}, ip={self.info.ip})"
