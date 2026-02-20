"""파일 관리 - 백업, 복원, 교체 (data/ 보호 보장)"""

import shutil
import zipfile
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class FileManager:
    """앱 코드 파일 관리자"""

    MAX_BACKUPS = 3  # 최대 백업 보관 수

    def __init__(self, base_dir: Path):
        self.base_dir = base_dir
        self.app_dir = base_dir / "app"
        self.backup_dir = base_dir / "backup"
        self._last_backup_path: Path = None

    def create_backup(self, version: str) -> bool:
        """현재 app/ 를 zip으로 백업"""
        if not self.app_dir.exists():
            logger.info("app/ 디렉터리 없음 - 백업 스킵")
            return True

        try:
            self.backup_dir.mkdir(exist_ok=True)
            backup_path = self.backup_dir / f"app_v{version}.zip"

            with zipfile.ZipFile(backup_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                for file_path in self.app_dir.rglob('*'):
                    if file_path.is_file():
                        # __pycache__ 는 백업하지 않음
                        if '__pycache__' in str(file_path):
                            continue
                        arc_name = file_path.relative_to(self.app_dir)
                        zf.write(file_path, arc_name)

            self._last_backup_path = backup_path
            self._cleanup_old_backups()
            logger.info(f"백업 생성: {backup_path}")
            return True

        except Exception as e:
            logger.error(f"백업 생성 실패: {e}")
            return False

    def replace_app(self, zip_path: Path) -> bool:
        """app/ 를 새 코드로 교체

        주의: data/, logs/, backup/ 등은 절대 건드리지 않음.
        오직 app/ 디렉터리만 교체.
        """
        try:
            # app/ 내용 삭제
            if self.app_dir.exists():
                shutil.rmtree(self.app_dir)

            self.app_dir.mkdir(exist_ok=True)

            # zip 해제
            with zipfile.ZipFile(zip_path, 'r') as zf:
                zf.extractall(self.app_dir)

            logger.info("앱 코드 교체 완료")
            return True

        except Exception as e:
            logger.error(f"앱 코드 교체 실패: {e}")
            return False

    def rollback(self) -> bool:
        """마지막 백업으로 복원"""
        if not self._last_backup_path or not self._last_backup_path.exists():
            # 가장 최신 백업 찾기
            if self.backup_dir.exists():
                backups = sorted(
                    self.backup_dir.glob("app_v*.zip"),
                    key=lambda p: p.stat().st_mtime,
                    reverse=True
                )
                if backups:
                    self._last_backup_path = backups[0]

        if not self._last_backup_path:
            logger.error("롤백할 백업이 없습니다.")
            return False

        try:
            logger.info(f"롤백 시작: {self._last_backup_path.name}")
            return self.replace_app(self._last_backup_path)
        except Exception as e:
            logger.error(f"롤백 실패: {e}")
            return False

    def _cleanup_old_backups(self):
        """오래된 백업 정리 (최대 3개 유지)"""
        if not self.backup_dir.exists():
            return

        backups = sorted(
            self.backup_dir.glob("app_v*.zip"),
            key=lambda p: p.stat().st_mtime,
            reverse=True
        )

        for old_backup in backups[self.MAX_BACKUPS:]:
            try:
                old_backup.unlink()
                logger.info(f"오래된 백업 삭제: {old_backup.name}")
            except Exception:
                pass
