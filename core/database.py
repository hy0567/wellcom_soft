"""SQLite Database for PC Device Management"""

import sqlite3
import os
from typing import List, Optional
from contextlib import contextmanager

from config import DB_PATH


class Database:
    """SQLite Database Manager"""

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._ensure_directory()
        self._init_db()

    def _ensure_directory(self):
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)

    @contextmanager
    def _get_connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def _init_db(self):
        """테이블 초기화"""
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # PCs table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS pcs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL,
                    agent_id TEXT NOT NULL,
                    ip TEXT DEFAULT '',
                    hostname TEXT DEFAULT '',
                    os_info TEXT DEFAULT '',
                    mac_address TEXT DEFAULT '',
                    group_name TEXT DEFAULT 'default',
                    screen_width INTEGER DEFAULT 1920,
                    screen_height INTEGER DEFAULT 1080,
                    memo TEXT DEFAULT '',
                    public_ip TEXT DEFAULT '',
                    keymap_name TEXT DEFAULT '',
                    script_name TEXT DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # 기존 DB 마이그레이션 — 새 컬럼 추가
            self._migrate_columns(cursor, 'pcs', {
                'memo': "TEXT DEFAULT ''",
                'public_ip': "TEXT DEFAULT ''",
                'keymap_name': "TEXT DEFAULT ''",
                'script_name': "TEXT DEFAULT ''",
            })

            # Groups table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS groups (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL,
                    description TEXT,
                    owner TEXT DEFAULT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # default 그룹
            cursor.execute('''
                INSERT OR IGNORE INTO groups (name, description)
                VALUES ('default', 'Default group')
            ''')

            conn.commit()

    @staticmethod
    def _migrate_columns(cursor, table: str, columns: dict):
        """기존 테이블에 누락된 컬럼 추가 (마이그레이션)"""
        cursor.execute(f"PRAGMA table_info({table})")
        existing = {row[1] for row in cursor.fetchall()}
        for col_name, col_def in columns.items():
            if col_name not in existing:
                cursor.execute(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_def}")

    # ==================== PC CRUD ====================

    def add_pc(self, name: str, agent_id: str, ip: str = "",
               hostname: str = "", os_info: str = "",
               group_name: str = "default") -> int:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO pcs (name, agent_id, ip, hostname, os_info, group_name)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (name, agent_id, ip, hostname, os_info, group_name))
            conn.commit()
            return cursor.lastrowid

    def update_pc(self, pc_id: int, **kwargs):
        allowed_fields = [
            'name', 'agent_id', 'ip', 'hostname', 'os_info',
            'mac_address', 'group_name', 'screen_width', 'screen_height',
            'memo', 'public_ip', 'keymap_name', 'script_name',
        ]
        updates = {k: v for k, v in kwargs.items() if k in allowed_fields}
        if not updates:
            return

        set_clause = ', '.join(f"{k} = ?" for k in updates.keys())
        values = list(updates.values()) + [pc_id]

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f'''
                UPDATE pcs SET {set_clause}, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            ''', values)
            conn.commit()

    def delete_pc(self, pc_id: int):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM pcs WHERE id = ?', (pc_id,))
            conn.commit()

    def get_pc(self, pc_id: int) -> Optional[dict]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM pcs WHERE id = ?', (pc_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_pc_by_name(self, name: str) -> Optional[dict]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM pcs WHERE name = ?', (name,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_pc_by_agent_id(self, agent_id: str) -> Optional[dict]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM pcs WHERE agent_id = ?', (agent_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_all_pcs(self) -> List[dict]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM pcs ORDER BY group_name, name')
            return [dict(row) for row in cursor.fetchall()]

    def get_pcs_by_group(self, group_name: str) -> List[dict]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'SELECT * FROM pcs WHERE group_name = ? ORDER BY name',
                (group_name,)
            )
            return [dict(row) for row in cursor.fetchall()]

    # ==================== Group CRUD ====================

    def add_group(self, name: str, description: str = "", owner: str = None) -> int:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO groups (name, description, owner) VALUES (?, ?, ?)
            ''', (name, description, owner))
            conn.commit()
            return cursor.lastrowid

    def delete_group(self, group_name: str):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE pcs SET group_name = 'default' WHERE group_name = ?
            ''', (group_name,))
            cursor.execute(
                'DELETE FROM groups WHERE name = ? AND name != "default"',
                (group_name,)
            )
            conn.commit()

    def get_all_groups(self, owner: str = None) -> List[dict]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if owner:
                cursor.execute(
                    'SELECT * FROM groups WHERE owner IS NULL OR owner = ? ORDER BY name',
                    (owner,)
                )
            else:
                cursor.execute('SELECT * FROM groups ORDER BY name')
            return [dict(row) for row in cursor.fetchall()]

    def get_pc_count(self) -> int:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT COUNT(*) FROM pcs')
            return cursor.fetchone()[0]

    def cleanup_orphan_pcs(self, valid_names: set):
        """유효하지 않은 PC 레코드 정리"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT id, name FROM pcs')
            for row in cursor.fetchall():
                if row['name'] not in valid_names:
                    cursor.execute('DELETE FROM pcs WHERE id = ?', (row['id'],))
            conn.commit()
