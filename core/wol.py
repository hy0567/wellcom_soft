"""Wake-on-LAN 매직 패킷 전송"""

import socket
import logging

logger = logging.getLogger(__name__)


def send_wol(mac_address: str, broadcast: str = '255.255.255.255', port: int = 9):
    """Wake-on-LAN 매직 패킷 전송

    Args:
        mac_address: MAC 주소 (xx:xx:xx:xx:xx:xx 또는 xx-xx-xx-xx-xx-xx)
        broadcast: 브로드캐스트 주소
        port: UDP 포트 (기본 9)
    """
    mac = mac_address.replace(':', '').replace('-', '').replace('.', '')
    if len(mac) != 12:
        logger.error(f"[WoL] 잘못된 MAC 주소: {mac_address}")
        return False

    try:
        mac_bytes = bytes.fromhex(mac)
    except ValueError:
        logger.error(f"[WoL] MAC 주소 파싱 실패: {mac_address}")
        return False

    # 매직 패킷: 6x 0xFF + 16x MAC
    magic = b'\xff' * 6 + mac_bytes * 16

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.sendto(magic, (broadcast, port))
        logger.info(f"[WoL] 매직 패킷 전송: {mac_address} → {broadcast}:{port}")
        return True
    except Exception as e:
        logger.error(f"[WoL] 전송 실패: {e}")
        return False
