"""다중 PC 관리자

에이전트 연결 관리자와 데이터베이스를 조합하여 다수의 원격 PC를 관리한다.
매니저가 각 에이전트 PC에 직접 WS 클라이언트로 접속하는 아키텍처.
"""

import re
import threading
import logging
from typing import Dict, List, Optional

from PyQt6.QtCore import QObject, pyqtSignal

from core.pc_device import PCDevice, PCInfo, PCStatus
from core.database import Database
from core.agent_server import AgentServer

logger = logging.getLogger(__name__)


def _natural_sort_key(text: str):
    """자연 정렬 키: 'PC1' < 'PC2' < 'PC10'"""
    return [int(c) if c.isdigit() else c.lower()
            for c in re.split(r'(\d+)', text)]


class DeviceSignals(QObject):
    """UI 업데이트를 위한 시그널"""
    device_added = pyqtSignal(str)            # pc_name
    device_removed = pyqtSignal(str)          # pc_name
    device_renamed = pyqtSignal(str, str)     # old_name, new_name
    device_status_changed = pyqtSignal(str)   # pc_name
    device_moved = pyqtSignal(str, str)       # pc_name, new_group
    devices_reloaded = pyqtSignal()


class PCManager:
    """다중 PC 관리자"""

    def __init__(self, agent_server: AgentServer):
        self.db = Database()
        self.pcs: Dict[str, PCDevice] = {}
        self.agent_server = agent_server
        self.signals = DeviceSignals()
        self._lock = threading.RLock()

        # 에이전트 서버 시그널 연결
        agent_server.agent_connected.connect(self._on_agent_connected)
        agent_server.agent_disconnected.connect(self._on_agent_disconnected)
        agent_server.thumbnail_received.connect(self._on_thumbnail_received)

    # ==================== PC 관리 ====================

    def load_from_db(self):
        """DB에서 PC 목록 로드"""
        with self._lock:
            self.pcs.clear()
            for row in self.db.get_all_pcs():
                info = PCInfo(
                    name=row['name'],
                    agent_id=row['agent_id'],
                    ip=row.get('ip', ''),
                    group=row.get('group_name', 'default'),
                    os_info=row.get('os_info', ''),
                    hostname=row.get('hostname', ''),
                    mac_address=row.get('mac_address', ''),
                    screen_width=row.get('screen_width', 1920),
                    screen_height=row.get('screen_height', 1080),
                )
                pc = PCDevice(info)
                self.pcs[row['name']] = pc

        self.signals.devices_reloaded.emit()
        logger.info(f"DB에서 {len(self.pcs)}개 PC 로드")

    def load_from_server(self):
        """서버 API에서 에이전트 목록을 가져와 로컬 PC 목록과 동기화"""
        from api_client import api_client

        if not api_client.is_logged_in:
            logger.warning("서버 미로그인 — 서버 동기화 스킵")
            return

        try:
            agents = api_client.get_agents()
        except Exception as e:
            logger.warning(f"서버 에이전트 목록 조회 실패: {e}")
            return

        with self._lock:
            server_agent_ids = set()

            for agent_data in agents:
                agent_id = agent_data.get('agent_id', '')
                if not agent_id:
                    continue
                server_agent_ids.add(agent_id)

                # display_name 또는 hostname을 PC 이름으로 사용
                display_name = agent_data.get('display_name') or ''
                hostname = agent_data.get('hostname', agent_id)
                name = display_name or hostname or agent_id

                # 이름 중복 방지
                base_name = name
                counter = 1
                while name in self.pcs and self.pcs[name].agent_id != agent_id:
                    name = f"{base_name} ({counter})"
                    counter += 1

                existing_pc = self.get_pc_by_agent_id(agent_id)
                if existing_pc:
                    # 기존 PC 정보 업데이트
                    existing_pc.update_info(
                        ip=agent_data.get('ip', ''),
                        os_info=agent_data.get('os_info', ''),
                        hostname=hostname,
                        mac_address=agent_data.get('mac_address', ''),
                        screen_width=agent_data.get('screen_width', 1920),
                        screen_height=agent_data.get('screen_height', 1080),
                    )
                    existing_pc.info.group = agent_data.get('group_name', 'default')
                    # 서버의 is_online 상태 반영
                    if agent_data.get('is_online') and not existing_pc.is_online:
                        existing_pc.status = PCStatus.CONNECTING
                        # 온라인 에이전트에 WS 직접 연결 시도
                        ip = agent_data.get('ip', '')
                        if ip and not self.agent_server.is_agent_connected(agent_id):
                            self.agent_server.connect_to_agent(
                                agent_id=agent_id, ip=ip,
                                hostname=hostname,
                                os_info=agent_data.get('os_info', ''),
                                screen_width=agent_data.get('screen_width', 1920),
                                screen_height=agent_data.get('screen_height', 1080),
                            )
                else:
                    # 새 PC 추가 (DB에도 저장)
                    info = PCInfo(
                        name=name,
                        agent_id=agent_id,
                        ip=agent_data.get('ip', ''),
                        group=agent_data.get('group_name', 'default'),
                        os_info=agent_data.get('os_info', ''),
                        hostname=hostname,
                        mac_address=agent_data.get('mac_address', ''),
                        screen_width=agent_data.get('screen_width', 1920),
                        screen_height=agent_data.get('screen_height', 1080),
                    )
                    pc = PCDevice(info)
                    self.pcs[name] = pc

                    # 로컬 DB에도 저장
                    try:
                        if not self.db.get_pc_by_agent_id(agent_id):
                            self.db.add_pc(
                                name=name, agent_id=agent_id,
                                ip=info.ip, hostname=info.hostname,
                                os_info=info.os_info,
                                group_name=info.group,
                            )
                    except Exception:
                        pass

                    # 온라인 에이전트에 WS 직접 연결 시도
                    if agent_data.get('is_online') and info.ip:
                        pc.status = PCStatus.CONNECTING
                        self.agent_server.connect_to_agent(
                            agent_id=agent_id, ip=info.ip,
                            hostname=hostname,
                            os_info=info.os_info,
                            screen_width=info.screen_width,
                            screen_height=info.screen_height,
                        )

        self.signals.devices_reloaded.emit()
        logger.info(f"서버에서 {len(agents)}개 에이전트 동기화 완료")

    def add_pc(self, name: str, agent_id: str, group: str = 'default',
               ip: str = '', hostname: str = '', os_info: str = '') -> Optional[PCDevice]:
        """PC 추가"""
        with self._lock:
            if name in self.pcs:
                logger.warning(f"PC 이름 중복: {name}")
                return None

            # DB 저장
            try:
                self.db.add_pc(
                    name=name, agent_id=agent_id, ip=ip,
                    hostname=hostname, os_info=os_info, group_name=group
                )
            except Exception as e:
                logger.error(f"PC 추가 실패: {e}")
                return None

            # 메모리에 추가
            info = PCInfo(
                name=name, agent_id=agent_id, ip=ip,
                group=group, os_info=os_info, hostname=hostname,
            )
            pc = PCDevice(info)
            self.pcs[name] = pc

        self.signals.device_added.emit(name)
        logger.info(f"PC 추가: {name} (agent_id={agent_id})")
        return pc

    def remove_pc(self, name: str) -> bool:
        """PC 제거"""
        with self._lock:
            pc = self.pcs.pop(name, None)
            if not pc:
                return False

            # DB 삭제
            db_row = self.db.get_pc_by_name(name)
            if db_row:
                self.db.delete_pc(db_row['id'])

        self.signals.device_removed.emit(name)
        logger.info(f"PC 제거: {name}")
        return True

    def rename_pc(self, old_name: str, new_name: str) -> bool:
        """PC 이름 변경"""
        with self._lock:
            if old_name not in self.pcs or new_name in self.pcs:
                return False

            pc = self.pcs.pop(old_name)
            pc.name = new_name
            self.pcs[new_name] = pc

            # DB 업데이트
            db_row = self.db.get_pc_by_name(old_name)
            if db_row:
                self.db.update_pc(db_row['id'], name=new_name)

        self.signals.device_renamed.emit(old_name, new_name)
        return True

    def move_pc_to_group(self, name: str, group: str):
        """PC 그룹 이동"""
        with self._lock:
            pc = self.pcs.get(name)
            if not pc:
                return

            pc.group = group

            db_row = self.db.get_pc_by_name(name)
            if db_row:
                self.db.update_pc(db_row['id'], group_name=group)

        self.signals.device_moved.emit(name, group)

    def get_pc(self, name: str) -> Optional[PCDevice]:
        with self._lock:
            return self.pcs.get(name)

    def get_pc_by_agent_id(self, agent_id: str) -> Optional[PCDevice]:
        with self._lock:
            for pc in self.pcs.values():
                if pc.agent_id == agent_id:
                    return pc
        return None

    def get_all_pcs(self) -> List[PCDevice]:
        with self._lock:
            return sorted(self.pcs.values(), key=lambda p: _natural_sort_key(p.name))

    def get_pcs_by_group(self, group: str) -> List[PCDevice]:
        with self._lock:
            return sorted(
                [p for p in self.pcs.values() if p.group == group],
                key=lambda p: _natural_sort_key(p.name)
            )

    def get_online_pcs(self) -> List[PCDevice]:
        with self._lock:
            return [p for p in self.pcs.values() if p.is_online]

    def get_groups(self) -> List[str]:
        """등록된 그룹 목록"""
        with self._lock:
            groups = set(p.group for p in self.pcs.values())
            groups.add('default')
        return sorted(groups, key=_natural_sort_key)

    def get_statistics(self) -> dict:
        with self._lock:
            total = len(self.pcs)
            online = sum(1 for p in self.pcs.values() if p.is_online)
        return {
            'total': total,
            'online': online,
            'offline': total - online,
        }

    # ==================== 에이전트 이벤트 핸들러 ====================

    def _on_agent_connected(self, agent_id: str, ip: str):
        """에이전트 연결 → PC 상태 ONLINE"""
        pc = self.get_pc_by_agent_id(agent_id)

        if not pc:
            # 새로운 에이전트 → 자동 등록
            info = self.agent_server.get_agent_info(agent_id)
            hostname = info.get('hostname', agent_id) if info else agent_id
            os_info = info.get('os_info', '') if info else ''
            name = hostname or agent_id

            # 이름 중복 방지
            with self._lock:
                base_name = name
                counter = 1
                while name in self.pcs:
                    name = f"{base_name} ({counter})"
                    counter += 1

            pc = self.add_pc(
                name=name, agent_id=agent_id, ip=ip,
                hostname=hostname, os_info=os_info,
            )
            if not pc:
                return

        # agent_server의 websocket 참조 가져오기
        ws = self.agent_server._agents.get(agent_id)

        with self._lock:
            pc.mark_online(ws, ip)

            # DB에 IP 업데이트
            db_row = self.db.get_pc_by_name(pc.name)
            if db_row:
                info = self.agent_server.get_agent_info(agent_id)
                update_kwargs = {'ip': ip}
                if info:
                    if info.get('hostname'):
                        update_kwargs['hostname'] = info['hostname']
                    if info.get('os_info'):
                        update_kwargs['os_info'] = info['os_info']
                    if info.get('screen_width'):
                        update_kwargs['screen_width'] = info['screen_width']
                    if info.get('screen_height'):
                        update_kwargs['screen_height'] = info['screen_height']
                self.db.update_pc(db_row['id'], **update_kwargs)

        self.signals.device_status_changed.emit(pc.name)
        logger.info(f"PC 온라인: {pc.name} ({ip})")

    def _on_agent_disconnected(self, agent_id: str):
        """에이전트 연결 해제 → PC 상태 OFFLINE"""
        pc = self.get_pc_by_agent_id(agent_id)
        if not pc:
            return

        with self._lock:
            pc.mark_offline()

        self.signals.device_status_changed.emit(pc.name)
        logger.info(f"PC 오프라인: {pc.name}")

    def _on_thumbnail_received(self, agent_id: str, jpeg_data: bytes):
        """썸네일 수신"""
        pc = self.get_pc_by_agent_id(agent_id)
        if pc:
            pc.update_thumbnail(jpeg_data)
