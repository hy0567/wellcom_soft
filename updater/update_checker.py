"""업데이트 확인 및 적용"""

import json
import shutil
import hashlib
import logging
from pathlib import Path
from typing import Optional, Tuple

from .github_client import GitHubClient, ReleaseInfo
from .file_manager import FileManager

logger = logging.getLogger(__name__)


def _compare_versions(current: str, latest: str) -> bool:
    """버전 비교 (latest > current 이면 True)
    packaging 없이 간단한 비교 (1.2.0 형식)
    """
    try:
        def parse(v):
            return tuple(int(x) for x in v.split('.'))
        return parse(latest) > parse(current)
    except Exception:
        return latest != current


class UpdateChecker:
    """업데이트 확인 및 적용 관리자"""

    def __init__(self, base_dir: Path, repo: str, token: str = None,
                 running_version: str = None, asset_name: str = "app.zip"):
        self.base_dir = base_dir
        self.app_dir = base_dir / "app"
        self.temp_dir = base_dir / "temp"
        self._running_version = running_version
        self.asset_name = asset_name
        self.github = GitHubClient(repo, token)
        self.file_manager = FileManager(base_dir)

    def get_current_version(self) -> str:
        """현재 설치된 버전 확인

        우선순위:
        1. 생성자에서 전달받은 버전 (실행 중인 코드의 __version__)
        2. version.json (업데이트 후 생성)
        3. version.py 파일 읽기 (app/ 디렉터리)
        4. import fallback
        """
        # 0) 실행 중인 코드에서 직접 전달받은 버전 (가장 정확)
        if self._running_version:
            return self._running_version

        # 1) version.json 확인 (업데이트 후 생성됨)
        version_file = self.app_dir / "version.json"
        try:
            if version_file.exists():
                data = json.loads(version_file.read_text(encoding='utf-8'))
                v = data.get("version", "0.0.0")
                logger.info(f"버전 감지 (version.json): {v}")
                return v
        except Exception:
            pass

        # 2) version.py에서 읽기
        try:
            version_py = self.app_dir / "version.py"
            if version_py.exists():
                content = version_py.read_text(encoding='utf-8')
                for line in content.split('\n'):
                    if line.startswith('__version__'):
                        v = line.split('=')[1].strip().strip('"\'')
                        logger.info(f"버전 감지 (version.py): {v} ({version_py})")
                        return v
            else:
                logger.warning(f"version.py 없음: {version_py}")
        except Exception as e:
            logger.warning(f"version.py 읽기 실패: {e}")

        # 3) 개발환경: version.py 직접 import
        try:
            from version import __version__
            logger.info(f"버전 감지 (import): {__version__}")
            return __version__
        except Exception:
            pass

        return "0.0.0"

    def check_update(self) -> Tuple[bool, Optional[ReleaseInfo]]:
        """업데이트 가능 여부 확인

        Returns:
            (update_available, release_info)
        """
        try:
            release = self.github.get_latest_release(asset_name=self.asset_name)
            if not release:
                return False, None

            current = self.get_current_version()
            if _compare_versions(current, release.version):
                logger.info(f"업데이트 발견: {current} -> {release.version}")
                return True, release
            else:
                logger.info(f"최신 버전 사용 중: {current}")
                return False, None
        except Exception as e:
            logger.error(f"업데이트 확인 실패: {e}")
            return False, None

    def apply_update(self, release: ReleaseInfo,
                     progress_callback=None) -> bool:
        """업데이트 적용

        1. 에셋(app.zip/agent.zip) 다운로드 -> temp/
        2. SHA256 체크섬 검증
        3. 현재 app/ 백업
        4. app/ 비우고 새 코드 압축 해제
        5. version.json 업데이트
        """
        try:
            self.temp_dir.mkdir(exist_ok=True)
            zip_path = self.temp_dir / self.asset_name

            # 1. 다운로드
            logger.info(f"다운로드 시작: v{release.version}")
            if not self.github.download_asset(release, str(zip_path),
                                              progress_callback):
                return False

            # 2. 체크섬 검증
            if release.checksum:
                actual = self._calculate_checksum(zip_path)
                if actual != release.checksum:
                    logger.error(
                        f"체크섬 불일치: expected={release.checksum[:16]}... "
                        f"actual={actual[:16]}..."
                    )
                    return False
                logger.info("체크섬 검증 통과")

            # 3. 백업
            old_version = self.get_current_version()
            self.file_manager.create_backup(old_version)

            # 4. app/ 교체
            if not self.file_manager.replace_app(zip_path):
                logger.error("앱 코드 교체 실패 - 롤백 시도")
                self.file_manager.rollback()
                return False

            # 5. version.json 생성
            version_data = {
                "version": release.version,
                "checksum": release.checksum,
                "updated_at": release.published_at
            }
            version_file = self.app_dir / "version.json"
            version_file.write_text(
                json.dumps(version_data, indent=2, ensure_ascii=False),
                encoding='utf-8'
            )

            logger.info(f"업데이트 완료: v{old_version} -> v{release.version}")
            return True

        except Exception as e:
            logger.error(f"업데이트 적용 실패: {e}")
            # 롤백 시도
            try:
                self.file_manager.rollback()
            except Exception:
                pass
            return False
        finally:
            # temp 정리
            if self.temp_dir.exists():
                shutil.rmtree(self.temp_dir, ignore_errors=True)

    @staticmethod
    def _calculate_checksum(file_path: Path) -> str:
        """SHA256 체크섬 계산"""
        sha256 = hashlib.sha256()
        with open(file_path, 'rb') as f:
            for chunk in iter(lambda: f.read(8192), b''):
                sha256.update(chunk)
        return sha256.hexdigest()
