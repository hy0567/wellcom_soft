"""순수 Python UPnP IGD 클라이언트 — miniupnpc 대체

외부 패키지 없이 UPnP 포트포워딩 수행.
SSDP(UDP 멀티캐스트) → 디바이스 XML → SOAP 제어.

사용:
    from upnp_helper import upnp_add_port_mapping, upnp_get_external_ip

    ok, ext_ip = upnp_add_port_mapping(21350, 'TCP', '192.168.1.100')
    ok, ext_ip = upnp_add_port_mapping(21350, 'UDP', '192.168.1.100')
    ext_ip = upnp_get_external_ip()
"""

import socket
import logging
import urllib.request
import urllib.error
from xml.etree import ElementTree
from typing import Optional

logger = logging.getLogger(__name__)

# SSDP 상수
SSDP_ADDR = '239.255.255.250'
SSDP_PORT = 1900
SSDP_MX = 3  # 최대 응답 대기 (초)

# UPnP 서비스 타입 (우선순위)
_SERVICE_TYPES = [
    'urn:schemas-upnp-org:service:WANIPConnection:1',
    'urn:schemas-upnp-org:service:WANIPConnection:2',
    'urn:schemas-upnp-org:service:WANPPPConnection:1',
]

# 캐시
_cached_control_url: Optional[str] = None
_cached_service_type: Optional[str] = None


def _ssdp_discover(timeout: float = 3.0) -> list[str]:
    """SSDP M-SEARCH로 UPnP IGD 디바이스 URL 목록 탐색

    Returns:
        location URL 목록
    """
    search_target = 'urn:schemas-upnp-org:device:InternetGatewayDevice:1'
    msg = (
        'M-SEARCH * HTTP/1.1\r\n'
        f'HOST: {SSDP_ADDR}:{SSDP_PORT}\r\n'
        'MAN: "ssdp:discover"\r\n'
        f'MX: {SSDP_MX}\r\n'
        f'ST: {search_target}\r\n'
        '\r\n'
    ).encode('utf-8')

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.settimeout(timeout)

    locations = []
    try:
        sock.sendto(msg, (SSDP_ADDR, SSDP_PORT))
        while True:
            try:
                data, addr = sock.recvfrom(4096)
                text = data.decode('utf-8', errors='replace')
                for line in text.split('\r\n'):
                    if line.lower().startswith('location:'):
                        loc = line.split(':', 1)[1].strip()
                        if loc not in locations:
                            locations.append(loc)
                            logger.debug(f"[UPnP-Pure] SSDP 발견: {loc}")
            except socket.timeout:
                break
    except Exception as e:
        logger.debug(f"[UPnP-Pure] SSDP 오류: {e}")
    finally:
        sock.close()

    return locations


def _fetch_xml(url: str, timeout: float = 5.0) -> Optional[ElementTree.Element]:
    """URL에서 XML 다운로드 및 파싱"""
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'WellcomAgent/1.0'})
        resp = urllib.request.urlopen(req, timeout=timeout)
        data = resp.read()
        return ElementTree.fromstring(data)
    except Exception as e:
        logger.debug(f"[UPnP-Pure] XML 가져오기 실패 ({url}): {e}")
        return None


def _find_control_url(device_url: str) -> Optional[tuple[str, str]]:
    """디바이스 XML에서 WANIPConnection 컨트롤 URL 추출

    Returns:
        (control_url, service_type) 또는 None
    """
    root = _fetch_xml(device_url)
    if root is None:
        return None

    # XML 네임스페이스 처리
    ns = {'upnp': 'urn:schemas-upnp-org:device-1-0'}

    # 모든 service 요소 탐색
    for service in root.iter():
        if service.tag.endswith('}service') or service.tag == 'service':
            st_elem = service.find('{urn:schemas-upnp-org:device-1-0}serviceType')
            if st_elem is None:
                # 네임스페이스 없이 재시도
                st_elem = service.find('serviceType')
            cu_elem = service.find('{urn:schemas-upnp-org:device-1-0}controlURL')
            if cu_elem is None:
                cu_elem = service.find('controlURL')

            if st_elem is not None and cu_elem is not None:
                st = st_elem.text or ''
                cu = cu_elem.text or ''
                if any(t in st for t in _SERVICE_TYPES):
                    # controlURL 절대 경로 변환
                    if cu.startswith('/'):
                        # device_url에서 base URL 추출
                        from urllib.parse import urlparse
                        parsed = urlparse(device_url)
                        cu = f"{parsed.scheme}://{parsed.netloc}{cu}"
                    elif not cu.startswith('http'):
                        base = device_url.rsplit('/', 1)[0]
                        cu = f"{base}/{cu}"

                    logger.debug(f"[UPnP-Pure] 서비스 발견: {st}")
                    logger.debug(f"[UPnP-Pure] 컨트롤 URL: {cu}")
                    return cu, st

    return None


def _soap_request(control_url: str, service_type: str,
                  action: str, args: dict, timeout: float = 5.0) -> Optional[str]:
    """SOAP 요청 전송

    Returns:
        응답 XML 문자열 또는 None
    """
    # SOAP 인자 XML 생성
    args_xml = ''
    for key, value in args.items():
        args_xml += f'<{key}>{value}</{key}>'

    body = (
        '<?xml version="1.0"?>'
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
        's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
        '<s:Body>'
        f'<u:{action} xmlns:u="{service_type}">'
        f'{args_xml}'
        f'</u:{action}>'
        '</s:Body>'
        '</s:Envelope>'
    ).encode('utf-8')

    headers = {
        'Content-Type': 'text/xml; charset="utf-8"',
        'SOAPAction': f'"{service_type}#{action}"',
        'User-Agent': 'WellcomAgent/1.0',
    }

    try:
        req = urllib.request.Request(control_url, data=body, headers=headers, method='POST')
        resp = urllib.request.urlopen(req, timeout=timeout)
        return resp.read().decode('utf-8', errors='replace')
    except urllib.error.HTTPError as e:
        error_body = e.read().decode('utf-8', errors='replace') if e.fp else ''
        logger.debug(f"[UPnP-Pure] SOAP 오류 {e.code}: {error_body[:200]}")
        return None
    except Exception as e:
        logger.debug(f"[UPnP-Pure] SOAP 요청 실패: {e}")
        return None


def _discover_and_cache() -> bool:
    """IGD 탐색 및 캐시"""
    global _cached_control_url, _cached_service_type

    if _cached_control_url:
        return True

    locations = _ssdp_discover(timeout=3.0)
    if not locations:
        logger.info("[UPnP-Pure] SSDP 라우터 발견 실패")
        return False

    for loc in locations:
        result = _find_control_url(loc)
        if result:
            _cached_control_url, _cached_service_type = result
            logger.info(f"[UPnP-Pure] IGD 컨트롤 URL 확보: {_cached_control_url}")
            return True

    logger.info("[UPnP-Pure] WANIPConnection 서비스를 찾을 수 없음")
    return False


def upnp_add_port_mapping(external_port: int, protocol: str,
                           internal_ip: str, internal_port: int = 0,
                           description: str = 'WellcomAgent',
                           duration: int = 0) -> tuple[bool, str]:
    """UPnP 포트 매핑 추가

    Args:
        external_port: 외부 포트
        protocol: 'TCP' 또는 'UDP'
        internal_ip: 내부 IP
        internal_port: 내부 포트 (0이면 external_port와 동일)
        description: 매핑 설명
        duration: 유효 시간 (0=영구)

    Returns:
        (성공여부, 외부IP)
    """
    if not _discover_and_cache():
        return False, ''

    if internal_port == 0:
        internal_port = external_port

    # 기존 매핑 삭제 시도 (충돌 방지)
    _soap_request(_cached_control_url, _cached_service_type,
                  'DeletePortMapping', {
                      'NewRemoteHost': '',
                      'NewExternalPort': str(external_port),
                      'NewProtocol': protocol,
                  })

    # 매핑 추가
    result = _soap_request(_cached_control_url, _cached_service_type,
                           'AddPortMapping', {
                               'NewRemoteHost': '',
                               'NewExternalPort': str(external_port),
                               'NewProtocol': protocol,
                               'NewInternalPort': str(internal_port),
                               'NewInternalClient': internal_ip,
                               'NewEnabled': '1',
                               'NewPortMappingDescription': description,
                               'NewLeaseDuration': str(duration),
                           })

    if result is None:
        logger.warning(f"[UPnP-Pure] {protocol} 포트 {external_port} 매핑 실패")
        return False, ''

    # 외부 IP 조회
    ext_ip = upnp_get_external_ip()
    logger.info(f"[UPnP-Pure] {protocol} 포트 {external_port} 매핑 성공 "
                f"(내부={internal_ip}:{internal_port}, 외부IP={ext_ip})")
    return True, ext_ip


def upnp_get_external_ip() -> str:
    """UPnP를 통한 외부 IP 조회

    Returns:
        외부 IP 문자열 또는 빈 문자열
    """
    if not _discover_and_cache():
        return ''

    result = _soap_request(_cached_control_url, _cached_service_type,
                           'GetExternalIPAddress', {})
    if not result:
        return ''

    try:
        root = ElementTree.fromstring(result)
        for elem in root.iter():
            if elem.tag.endswith('NewExternalIPAddress') or elem.tag == 'NewExternalIPAddress':
                return elem.text or ''
    except Exception:
        pass

    return ''


def upnp_delete_port_mapping(external_port: int, protocol: str) -> bool:
    """UPnP 포트 매핑 삭제

    Returns:
        성공 여부
    """
    if not _cached_control_url:
        return False

    result = _soap_request(_cached_control_url, _cached_service_type,
                           'DeletePortMapping', {
                               'NewRemoteHost': '',
                               'NewExternalPort': str(external_port),
                               'NewProtocol': protocol,
                           })
    return result is not None
