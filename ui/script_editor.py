"""스크립트 에디터 다이얼로그

스크립트 작성/편집, 명령 목록, 실행 로그 표시.
"""

import logging
from typing import Optional

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QSplitter,
    QTextEdit, QListWidget, QListWidgetItem,
    QPushButton, QLabel, QLineEdit, QGroupBox,
    QFormLayout, QComboBox, QSpinBox, QCheckBox,
    QToolBar, QWidget, QMessageBox, QInputDialog,
    QPlainTextEdit,
)
from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QAction, QFont, QTextCharFormat, QColor, QSyntaxHighlighter

from core.script_engine import (
    ScriptEngine, ScriptParser, ScriptInfo, CommandType,
)

logger = logging.getLogger(__name__)


class ScriptSyntaxHighlighter(QSyntaxHighlighter):
    """스크립트 구문 강조"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rules = []

        # 명령어 — 파란색
        cmd_fmt = QTextCharFormat()
        cmd_fmt.setForeground(QColor('#569cd6'))
        cmd_fmt.setFontWeight(QFont.Weight.Bold)
        commands = '|'.join(ct.value for ct in CommandType)
        aliases = 'tap|dclick|lpress|wait|sleep|type|press|exec|run|img'
        self._rules.append((f'^\\s*({commands}|{aliases})\\b', cmd_fmt))

        # 숫자 — 연두색
        num_fmt = QTextCharFormat()
        num_fmt.setForeground(QColor('#b5cea8'))
        self._rules.append((r'\b\d+\.?\d*\b', num_fmt))

        # 문자열 — 주황색
        str_fmt = QTextCharFormat()
        str_fmt.setForeground(QColor('#ce9178'))
        self._rules.append((r'"[^"]*"', str_fmt))
        self._rules.append((r"'[^']*'", str_fmt))

        # 주석 — 회색
        comment_fmt = QTextCharFormat()
        comment_fmt.setForeground(QColor('#6a9955'))
        self._rules.append((r'#.*$', comment_fmt))
        self._rules.append((r'//.*$', comment_fmt))

        # 라벨 — 노란색
        label_fmt = QTextCharFormat()
        label_fmt.setForeground(QColor('#dcdcaa'))
        self._rules.append((r'^label\s+\S+', label_fmt))

        import re
        self._compiled = [(re.compile(p, re.MULTILINE), fmt) for p, fmt in self._rules]

    def highlightBlock(self, text: str):
        for pattern, fmt in self._compiled:
            for match in pattern.finditer(text):
                self.setFormat(match.start(), match.end() - match.start(), fmt)


class ScriptEditorDialog(QDialog):
    """스크립트 에디터 다이얼로그"""

    def __init__(self, script_engine: ScriptEngine, script_name: str = "",
                 parent=None):
        super().__init__(parent)
        self.engine = script_engine
        self._current_name = script_name

        self.setWindowTitle(f"스크립트 에디터 — {script_name}" if script_name else "스크립트 에디터")
        self.setMinimumSize(800, 600)
        self._init_ui()

        if script_name:
            self._load_script(script_name)

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        # 상단: 툴바
        toolbar = QHBoxLayout()

        self.name_edit = QLineEdit(self._current_name)
        self.name_edit.setPlaceholderText("스크립트 이름")
        self.name_edit.setMaximumWidth(200)
        toolbar.addWidget(QLabel("이름:"))
        toolbar.addWidget(self.name_edit)

        toolbar.addStretch()

        self.btn_save = QPushButton("저장")
        self.btn_save.clicked.connect(self._save_script)
        toolbar.addWidget(self.btn_save)

        self.btn_validate = QPushButton("검증")
        self.btn_validate.clicked.connect(self._validate)
        toolbar.addWidget(self.btn_validate)

        layout.addLayout(toolbar)

        # 스플리터: 에디터 + 명령 참조
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # 좌측: 코드 에디터
        editor_widget = QWidget()
        editor_layout = QVBoxLayout(editor_widget)
        editor_layout.setContentsMargins(0, 0, 0, 0)

        self.editor = QPlainTextEdit()
        self.editor.setFont(QFont("Consolas", 11))
        self.editor.setPlaceholderText(
            "# 스크립트 명령 입력\n"
            "# 예시:\n"
            "click 500 300\n"
            "delay 1000\n"
            "key enter\n"
            "loop 3\n"
            "  click 100 200\n"
            "  delay 500\n"
            "loop_end\n"
        )
        self.highlighter = ScriptSyntaxHighlighter(self.editor.document())
        editor_layout.addWidget(QLabel("스크립트 코드:"))
        editor_layout.addWidget(self.editor, 3)

        # 로그
        self.log_output = QPlainTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setFont(QFont("Consolas", 9))
        self.log_output.setMaximumHeight(150)
        editor_layout.addWidget(QLabel("로그:"))
        editor_layout.addWidget(self.log_output, 1)

        splitter.addWidget(editor_widget)

        # 우측: 명령 참조
        ref_widget = QWidget()
        ref_layout = QVBoxLayout(ref_widget)
        ref_layout.setContentsMargins(0, 0, 0, 0)
        ref_layout.addWidget(QLabel("명령 참조:"))

        self.cmd_list = QListWidget()
        self.cmd_list.setMaximumWidth(250)
        self.cmd_list.itemDoubleClicked.connect(self._insert_command)

        commands_info = [
            ("click x y", "좌표 클릭"),
            ("double_click x y", "더블클릭"),
            ("long_press x y", "길게 누르기"),
            ("drag x1 y1 x2 y2", "드래그"),
            ("swipe x1 y1 x2 y2 ms", "스와이프"),
            ("scroll up/down N", "스크롤"),
            ("key 키이름", "키 입력 (예: enter, ctrl+c)"),
            ("text \"문자열\"", "텍스트 입력"),
            ("delay ms", "대기 (밀리초)"),
            ("loop N", "반복 시작 (infinite 가능)"),
            ("loop_end", "반복 끝"),
            ("if_image \"파일\" 임계값", "이미지 조건 (OpenCV)"),
            ("else", "조건 else"),
            ("endif", "조건 끝"),
            ("label 이름", "라벨 정의"),
            ("goto 이름", "라벨로 이동"),
            ("log \"메시지\"", "로그 출력"),
            ("screenshot", "스크린샷 요청"),
            ("command 명령어", "원격 명령 실행"),
            ("stop", "스크립트 중지"),
        ]

        for cmd, desc in commands_info:
            item = QListWidgetItem(f"{cmd}  —  {desc}")
            item.setData(Qt.ItemDataRole.UserRole, cmd.split()[0])
            self.cmd_list.addItem(item)

        ref_layout.addWidget(self.cmd_list)
        splitter.addWidget(ref_widget)

        splitter.setSizes([600, 200])
        layout.addWidget(splitter)

        # 하단 버튼
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self.btn_ok = QPushButton("확인")
        self.btn_ok.clicked.connect(self._ok)
        btn_layout.addWidget(self.btn_ok)

        self.btn_cancel = QPushButton("취소")
        self.btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(self.btn_cancel)

        layout.addLayout(btn_layout)

    def _load_script(self, name: str):
        info = self.engine.get_script(name)
        if info:
            text = ScriptParser.to_text(info.commands)
            self.editor.setPlainText(text)

    def _save_script(self):
        name = self.name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "오류", "스크립트 이름을 입력하세요.")
            return

        text = self.editor.toPlainText()

        if self.engine.get_script(name):
            self.engine.update_script(name, text)
        else:
            self.engine.add_script(name, text)

        self._current_name = name
        self.setWindowTitle(f"스크립트 에디터 — {name}")
        self.log_output.appendPlainText(f"저장 완료: {name}")

    def _validate(self):
        text = self.editor.toPlainText()
        try:
            commands = ScriptParser.parse(text)
            self.log_output.appendPlainText(
                f"검증 OK: {len(commands)}개 명령 파싱 완료"
            )
        except Exception as e:
            self.log_output.appendPlainText(f"검증 실패: {e}")

    def _insert_command(self, item: QListWidgetItem):
        cmd = item.data(Qt.ItemDataRole.UserRole)
        if cmd:
            cursor = self.editor.textCursor()
            cursor.insertText(f"{cmd} ")
            self.editor.setFocus()

    def _ok(self):
        self._save_script()
        self.accept()


class ScriptListDialog(QDialog):
    """스크립트 목록 관리 다이얼로그"""

    def __init__(self, script_engine: ScriptEngine, parent=None):
        super().__init__(parent)
        self.engine = script_engine
        self.setWindowTitle("스크립트 관리")
        self.setMinimumSize(500, 400)
        self._init_ui()
        self._refresh_list()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        # 도구 버튼
        btn_layout = QHBoxLayout()
        self.btn_new = QPushButton("새 스크립트")
        self.btn_new.clicked.connect(self._new_script)
        btn_layout.addWidget(self.btn_new)

        self.btn_edit = QPushButton("편집")
        self.btn_edit.clicked.connect(self._edit_script)
        btn_layout.addWidget(self.btn_edit)

        self.btn_rename = QPushButton("이름 변경")
        self.btn_rename.clicked.connect(self._rename_script)
        btn_layout.addWidget(self.btn_rename)

        self.btn_delete = QPushButton("삭제")
        self.btn_delete.clicked.connect(self._delete_script)
        btn_layout.addWidget(self.btn_delete)

        btn_layout.addStretch()
        layout.addLayout(btn_layout)

        # 목록
        self.list_widget = QListWidget()
        self.list_widget.itemDoubleClicked.connect(self._edit_script)
        layout.addWidget(self.list_widget)

        # 닫기
        close_btn = QPushButton("닫기")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn, alignment=Qt.AlignmentFlag.AlignRight)

    def _refresh_list(self):
        self.list_widget.clear()
        for script in self.engine.get_scripts():
            desc = script.description or f"{len(script.commands)}개 명령"
            item = QListWidgetItem(f"{script.name}  —  {desc}")
            item.setData(Qt.ItemDataRole.UserRole, script.name)
            self.list_widget.addItem(item)

    def _new_script(self):
        name, ok = QInputDialog.getText(self, "새 스크립트", "스크립트 이름:")
        if ok and name.strip():
            dlg = ScriptEditorDialog(self.engine, "", self)
            dlg.name_edit.setText(name.strip())
            if dlg.exec():
                self._refresh_list()

    def _edit_script(self):
        item = self.list_widget.currentItem()
        if not item:
            return
        name = item.data(Qt.ItemDataRole.UserRole)
        dlg = ScriptEditorDialog(self.engine, name, self)
        if dlg.exec():
            self._refresh_list()

    def _rename_script(self):
        item = self.list_widget.currentItem()
        if not item:
            return
        old_name = item.data(Qt.ItemDataRole.UserRole)
        new_name, ok = QInputDialog.getText(self, "이름 변경", "새 이름:", text=old_name)
        if ok and new_name.strip() and new_name.strip() != old_name:
            self.engine.rename_script(old_name, new_name.strip())
            self._refresh_list()

    def _delete_script(self):
        item = self.list_widget.currentItem()
        if not item:
            return
        name = item.data(Qt.ItemDataRole.UserRole)
        reply = QMessageBox.question(
            self, "삭제 확인", f"'{name}' 스크립트를 삭제하시겠습니까?",
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.engine.delete_script(name)
            self._refresh_list()
