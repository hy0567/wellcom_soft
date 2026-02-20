"""WellcomSOFT API 서버 설정"""
import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    _env_path = Path(__file__).parent / ".env"
    if _env_path.exists():
        load_dotenv(_env_path)
except ImportError:
    pass

# MySQL
DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_USER = os.getenv("DB_USER", "wellcom_api")
DB_PASS = os.getenv("DB_PASS", "Wellcom@API2026!")
DB_NAME = os.getenv("DB_NAME", "wellcomsoft")

# JWT
JWT_SECRET = os.getenv("JWT_SECRET", "wellcomsoft-jwt-secret-key-2026-change-in-production")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = 24

# Server
API_HOST = os.getenv("API_HOST", "0.0.0.0")
API_PORT = int(os.getenv("API_PORT", "4797"))

# File Storage
UPLOAD_DIR = os.getenv("UPLOAD_DIR", "/opt/wellcomsoft/uploads")
MAX_FILE_SIZE = 500 * 1024 * 1024  # 500MB
