"""GitHub Releases API 클라이언트 (Private repo 지원)"""

import requests
import logging
from typing import Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class ReleaseInfo:
    """릴리스 정보"""
    version: str            # "1.2.0"
    download_url: str       # app.zip의 browser_download_url
    checksum: str           # SHA256 (release body에서 파싱)
    release_notes: str      # 릴리스 노트
    published_at: str       # 게시 일시
    asset_id: int = 0       # Private repo 다운로드용 asset ID


class GitHubClient:
    """GitHub Releases API 클라이언트"""

    API_BASE = "https://api.github.com"

    def __init__(self, repo: str, token: Optional[str] = None):
        """
        Args:
            repo: "owner/repo" 형식 (예: "hy0567/wellcom_soft")
            token: GitHub Personal Access Token (Private repo 필수)
        """
        self.repo = repo
        self.token = token
        self.headers = {"Accept": "application/vnd.github+json"}
        if token:
            self.headers["Authorization"] = f"Bearer {token}"

    def get_latest_release(self, asset_name: str = "app.zip") -> Optional[ReleaseInfo]:
        """최신 릴리스 정보 조회

        Args:
            asset_name: 다운로드할 에셋 파일명 (기본 "app.zip", 에이전트는 "agent.zip")
        """
        url = f"{self.API_BASE}/repos/{self.repo}/releases/latest"
        try:
            resp = requests.get(url, headers=self.headers, timeout=10)
            if resp.status_code == 404:
                logger.info("릴리스가 없습니다.")
                return None
            if resp.status_code == 401:
                logger.error("GitHub 인증 실패 - 토큰을 확인하세요.")
                return None
            resp.raise_for_status()
            data = resp.json()

            # 지정된 에셋 찾기
            download_url = None
            asset_id = 0
            for asset in data.get("assets", []):
                if asset["name"] == asset_name:
                    download_url = asset["browser_download_url"]
                    asset_id = asset.get("id", 0)
                    break

            if not download_url:
                logger.warning(f"릴리스에 {asset_name} 에셋이 없습니다.")
                return None

            # body에서 SHA256 체크섬 파싱
            checksum = self._parse_checksum(data.get("body", ""), asset_name)

            return ReleaseInfo(
                version=data["tag_name"].lstrip("v"),
                download_url=download_url,
                checksum=checksum,
                release_notes=data.get("body", ""),
                published_at=data.get("published_at", ""),
                asset_id=asset_id,
            )
        except requests.exceptions.ConnectionError:
            logger.warning("네트워크 연결 실패 - 오프라인 모드")
            return None
        except requests.exceptions.Timeout:
            logger.warning("GitHub API 타임아웃")
            return None
        except Exception as e:
            logger.error(f"GitHub API 호출 실패: {e}")
            return None

    def download_asset(self, release_info: ReleaseInfo, dest_path: str,
                       progress_callback=None) -> bool:
        """에셋 다운로드 (Private repo: API 경유, Public: 직접 다운로드)"""
        try:
            if self.token and release_info.asset_id:
                # Private repo: API를 통해 다운로드
                url = (f"{self.API_BASE}/repos/{self.repo}"
                       f"/releases/assets/{release_info.asset_id}")
                headers = {
                    "Authorization": f"Bearer {self.token}",
                    "Accept": "application/octet-stream",
                }
            else:
                # Public repo: 직접 다운로드
                url = release_info.download_url
                headers = {}

            resp = requests.get(url, headers=headers, stream=True, timeout=60)
            resp.raise_for_status()

            total_size = int(resp.headers.get('content-length', 0))
            downloaded = 0

            with open(dest_path, 'wb') as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress_callback and total_size:
                        progress_callback(downloaded, total_size)

            logger.info(f"다운로드 완료: {dest_path} ({downloaded} bytes)")
            return True
        except Exception as e:
            logger.error(f"다운로드 실패: {e}")
            return False

    @staticmethod
    def _parse_checksum(body: str, asset_name: str = "app.zip") -> str:
        """릴리스 노트에서 SHA256 체크섬 추출

        지원 형식:
        - "SHA256: abcdef..."                    (단일 에셋)
        - "SHA256(app.zip): abcdef..."           (다중 에셋)
        - "SHA256(agent.zip): 123456..."         (다중 에셋)
        """
        for line in body.split('\n'):
            upper_line = line.upper()
            if 'SHA256' not in upper_line:
                continue

            # 다중 에셋 형식: "SHA256(app.zip): abcdef..."
            if f'SHA256({asset_name})' in line:
                parts = line.split(':', 1)
                if len(parts) >= 2:
                    return parts[1].strip()

            # 단일 에셋 형식 (레거시): "SHA256: abcdef..."
            if '(' not in line and ':' in line:
                parts = line.split(':')
                if len(parts) >= 2:
                    return parts[-1].strip()

        return ""
