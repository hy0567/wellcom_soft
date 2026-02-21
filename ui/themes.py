"""WellcomSOFT 테마 시스템 (다크/라이트)"""


DARK_THEME = """
QMainWindow, QDialog {
    background-color: #1e1e1e;
    color: #d4d4d4;
}
QMenuBar {
    background-color: #2d2d2d;
    color: #d4d4d4;
    border-bottom: 1px solid #3e3e3e;
}
QMenuBar::item:selected {
    background-color: #094771;
}
QMenu {
    background-color: #2d2d2d;
    color: #d4d4d4;
    border: 1px solid #3e3e3e;
}
QMenu::item:selected {
    background-color: #094771;
}
QToolBar {
    background-color: #2d2d2d;
    border-bottom: 1px solid #3e3e3e;
    spacing: 4px;
    padding: 2px;
}
QToolBar QToolButton {
    background-color: transparent;
    color: #d4d4d4;
    border: 1px solid transparent;
    border-radius: 3px;
    padding: 4px 8px;
}
QToolBar QToolButton:hover {
    background-color: #3e3e3e;
    border: 1px solid #555;
}
QToolBar QToolButton:pressed {
    background-color: #094771;
}
QToolBar QToolButton:checked {
    background-color: #094771;
    border: 1px solid #1177bb;
}
QStatusBar {
    background-color: #007acc;
    color: #ffffff;
    font-size: 12px;
}
QStatusBar QLabel {
    color: #ffffff;
    padding: 0 6px;
}
QTreeWidget {
    background-color: #252526;
    color: #d4d4d4;
    border: none;
    outline: none;
    font-size: 12px;
}
QTreeWidget::item {
    padding: 4px 2px;
    border: none;
}
QTreeWidget::item:selected {
    background-color: #094771;
    color: #ffffff;
}
QTreeWidget::item:hover {
    background-color: #2a2d2e;
}
QTreeWidget::branch {
    background-color: #252526;
}
QHeaderView::section {
    background-color: #2d2d2d;
    color: #d4d4d4;
    border: 1px solid #3e3e3e;
    padding: 4px;
    font-weight: bold;
}
QTabWidget::pane {
    border: 1px solid #3e3e3e;
    background-color: #1e1e1e;
}
QTabBar::tab {
    background-color: #2d2d2d;
    color: #969696;
    padding: 6px 16px;
    border: 1px solid #3e3e3e;
    border-bottom: none;
    min-width: 80px;
}
QTabBar::tab:selected {
    background-color: #1e1e1e;
    color: #ffffff;
    border-bottom: 2px solid #007acc;
}
QTabBar::tab:hover:!selected {
    background-color: #353535;
}
QScrollArea {
    background-color: #1e1e1e;
    border: none;
}
QScrollBar:vertical {
    background-color: #1e1e1e;
    width: 12px;
}
QScrollBar::handle:vertical {
    background-color: #424242;
    min-height: 20px;
    border-radius: 4px;
    margin: 2px;
}
QScrollBar::handle:vertical:hover {
    background-color: #555;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}
QScrollBar:horizontal {
    background-color: #1e1e1e;
    height: 12px;
}
QScrollBar::handle:horizontal {
    background-color: #424242;
    min-width: 20px;
    border-radius: 4px;
    margin: 2px;
}
QScrollBar::handle:horizontal:hover {
    background-color: #555;
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0;
}
QSplitter::handle {
    background-color: #3e3e3e;
    width: 2px;
}
QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {
    background-color: #3c3c3c;
    color: #d4d4d4;
    border: 1px solid #3e3e3e;
    border-radius: 3px;
    padding: 4px 8px;
    selection-background-color: #094771;
}
QLineEdit:focus, QTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {
    border: 1px solid #007acc;
}
QPushButton {
    background-color: #0e639c;
    color: #ffffff;
    border: none;
    border-radius: 3px;
    padding: 6px 14px;
    font-size: 12px;
}
QPushButton:hover {
    background-color: #1177bb;
}
QPushButton:pressed {
    background-color: #094771;
}
QPushButton:disabled {
    background-color: #3e3e3e;
    color: #666;
}
QCheckBox {
    color: #d4d4d4;
    spacing: 6px;
}
QLabel {
    color: #d4d4d4;
}
QGroupBox {
    color: #d4d4d4;
    border: 1px solid #3e3e3e;
    border-radius: 4px;
    margin-top: 8px;
    padding-top: 12px;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 4px;
}
QSlider::groove:horizontal {
    height: 4px;
    background: #3e3e3e;
    border-radius: 2px;
}
QSlider::handle:horizontal {
    background: #007acc;
    width: 14px;
    height: 14px;
    margin: -5px 0;
    border-radius: 7px;
}
QToolTip {
    background-color: #2d2d2d;
    color: #d4d4d4;
    border: 1px solid #3e3e3e;
    padding: 4px;
}
"""


LIGHT_THEME = """
QMainWindow, QDialog {
    background-color: #f3f3f3;
    color: #1e1e1e;
}
QMenuBar {
    background-color: #e8e8e8;
    color: #1e1e1e;
    border-bottom: 1px solid #d0d0d0;
}
QMenuBar::item:selected {
    background-color: #c8ddf3;
}
QMenu {
    background-color: #f0f0f0;
    color: #1e1e1e;
    border: 1px solid #d0d0d0;
}
QMenu::item:selected {
    background-color: #c8ddf3;
}
QToolBar {
    background-color: #e8e8e8;
    border-bottom: 1px solid #d0d0d0;
    spacing: 4px;
    padding: 2px;
}
QToolBar QToolButton {
    background-color: transparent;
    color: #1e1e1e;
    border: 1px solid transparent;
    border-radius: 3px;
    padding: 4px 8px;
}
QToolBar QToolButton:hover {
    background-color: #d0d0d0;
    border: 1px solid #bbb;
}
QToolBar QToolButton:pressed {
    background-color: #c8ddf3;
}
QToolBar QToolButton:checked {
    background-color: #c8ddf3;
    border: 1px solid #007acc;
}
QStatusBar {
    background-color: #007acc;
    color: #ffffff;
    font-size: 12px;
}
QStatusBar QLabel {
    color: #ffffff;
    padding: 0 6px;
}
QTreeWidget {
    background-color: #ffffff;
    color: #1e1e1e;
    border: 1px solid #d0d0d0;
    outline: none;
    font-size: 12px;
}
QTreeWidget::item {
    padding: 4px 2px;
}
QTreeWidget::item:selected {
    background-color: #c8ddf3;
    color: #1e1e1e;
}
QTreeWidget::item:hover {
    background-color: #e8f0fe;
}
QHeaderView::section {
    background-color: #e8e8e8;
    color: #1e1e1e;
    border: 1px solid #d0d0d0;
    padding: 4px;
    font-weight: bold;
}
QTabWidget::pane {
    border: 1px solid #d0d0d0;
    background-color: #f3f3f3;
}
QTabBar::tab {
    background-color: #e8e8e8;
    color: #666;
    padding: 6px 16px;
    border: 1px solid #d0d0d0;
    border-bottom: none;
    min-width: 80px;
}
QTabBar::tab:selected {
    background-color: #f3f3f3;
    color: #1e1e1e;
    border-bottom: 2px solid #007acc;
}
QScrollArea {
    background-color: #f3f3f3;
    border: none;
}
QScrollBar:vertical {
    background-color: #f3f3f3;
    width: 12px;
}
QScrollBar::handle:vertical {
    background-color: #c0c0c0;
    min-height: 20px;
    border-radius: 4px;
    margin: 2px;
}
QScrollBar::handle:vertical:hover {
    background-color: #999;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}
QScrollBar:horizontal {
    background-color: #f3f3f3;
    height: 12px;
}
QScrollBar::handle:horizontal {
    background-color: #c0c0c0;
    min-width: 20px;
    border-radius: 4px;
    margin: 2px;
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0;
}
QSplitter::handle {
    background-color: #d0d0d0;
    width: 2px;
}
QLineEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {
    background-color: #ffffff;
    color: #1e1e1e;
    border: 1px solid #d0d0d0;
    border-radius: 3px;
    padding: 4px 8px;
    selection-background-color: #c8ddf3;
}
QLineEdit:focus, QTextEdit:focus, QSpinBox:focus, QComboBox:focus {
    border: 1px solid #007acc;
}
QPushButton {
    background-color: #0e639c;
    color: #ffffff;
    border: none;
    border-radius: 3px;
    padding: 6px 14px;
    font-size: 12px;
}
QPushButton:hover {
    background-color: #1177bb;
}
QPushButton:pressed {
    background-color: #094771;
}
QPushButton:disabled {
    background-color: #d0d0d0;
    color: #999;
}
QCheckBox {
    color: #1e1e1e;
    spacing: 6px;
}
QLabel {
    color: #1e1e1e;
}
QGroupBox {
    color: #1e1e1e;
    border: 1px solid #d0d0d0;
    border-radius: 4px;
    margin-top: 8px;
    padding-top: 12px;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 4px;
}
QSlider::groove:horizontal {
    height: 4px;
    background: #d0d0d0;
    border-radius: 2px;
}
QSlider::handle:horizontal {
    background: #007acc;
    width: 14px;
    height: 14px;
    margin: -5px 0;
    border-radius: 7px;
}
QToolTip {
    background-color: #f0f0f0;
    color: #1e1e1e;
    border: 1px solid #d0d0d0;
    padding: 4px;
}
"""


def get_theme_stylesheet(theme_name: str) -> str:
    """테마 이름으로 QSS 반환"""
    if theme_name == 'light':
        return LIGHT_THEME
    return DARK_THEME
