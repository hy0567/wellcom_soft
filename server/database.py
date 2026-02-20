"""MySQL 데이터베이스 연결 관리"""
import pymysql
from contextlib import contextmanager
from config import DB_HOST, DB_PORT, DB_USER, DB_PASS, DB_NAME


def get_connection():
    return pymysql.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASS,
        database=DB_NAME,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
    )


@contextmanager
def get_db():
    conn = get_connection()
    try:
        yield conn
    finally:
        conn.close()
