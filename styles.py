# styles.py — Qt stylesheet for the ASWAXS Sample Station main window.
# Debug entry point: check objectName strings in setStyleSheet selectors match widget setObjectName calls.
# External deps: none.

from PyQt6.QtWidgets import QWidget


def apply_style(widget: QWidget) -> None:
    widget.setStyleSheet("""
    /* ── Base ── */
    QWidget {
        font-family: "Segoe UI", "Helvetica Neue", Arial, sans-serif;
        font-size: 9pt;
        color: #20242a;
        background-color: #f3f4f6;
    }

    /* ── Motor bar frame ── */
    QFrame#motorBar {
        background: #ffffff;
        border: 1px solid #c8ccd2;
        border-radius: 6px;
    }
    QFrame#motorSep {
        color: #c8ccd2;
        max-height: 1px;
        margin: 0 0;
    }

    /* ── Axis badge ── */
    QLabel#axisLabel {
        background: #2f6fae;
        color: white;
        border-radius: 4px;
        font-weight: bold;
        font-size: 9pt;
        padding: 1px 2px;
    }

    /* ── Field tags (RBV, SP, Step) ── */
    QLabel#fieldTag {
        color: #6b7a8d;
        font-size: 8pt;
    }

    /* ── RBV readback ── */
    QLabel#rbvLabel {
        font-family: "Consolas", "Courier New", monospace;
        font-size: 9pt;
        color: #15803d;
        background: #f0fdf4;
        border: 1px solid #bbf7d0;
        border-radius: 3px;
        padding: 1px 4px;
    }

    /* ── MOVN indicator ── */
    QLabel#movnIdle   { color: #c8ccd2; font-size: 11pt; background: transparent; }
    QLabel#movnActive { color: #e07b00; font-size: 11pt; background: transparent; }

    /* ── Offset / status labels ── */
    QLabel#offsetLabel {
        color: #6b7a8d;
        font-style: italic;
        font-size: 8.5pt;
        background: transparent;
    }
    QLabel#focusLabel {
        font-family: "Consolas", monospace;
        color: #1f4f7f;
        background: #edf4fd;
        border: 1px solid #b9c0ca;
        border-radius: 3px;
        padding: 1px 4px;
    }
    QPushButton#cancelBtn {
        background: #dc2626;
        color: #ffffff;
        border: none;
        border-radius: 4px;
        padding: 3px 10px;
    }
    QPushButton#cancelBtn:hover {
        background: #b91c1c;
    }
    QLabel#eguLabel {
        color: #6b7a8d;
        font-size: 7.5pt;
        background: transparent;
    }
    QLabel#camStateLabel {
        color: #6b7a8d;
        font-size: 8.5pt;
        background: transparent;
    }
    QLabel#statusLabel {
        font-family: "Consolas", monospace;
        font-size: 8.5pt;
        color: #20242a;
        background: transparent;
    }
    QLabel#descLabel { background: transparent; }

    /* ── Buttons (base) ── */
    QPushButton {
        background: #f8f9fb;
        border: 1px solid #b9c0ca;
        border-radius: 4px;
        padding: 3px 10px;
        min-height: 24px;
        color: #20242a;
    }
    QPushButton:hover   { background: #edf4fd; border-color: #2f6fae; }
    QPushButton:pressed { background: #2f6fae; color: white; border-color: #1f4f7f; }
    QPushButton:disabled { color: #a0a8b4; background: #f3f4f6; border-color: #dde1e7; }

    /* ── Tweak (◀▶) buttons ── */
    QPushButton#tweakBtn {
        background: #edf4fd;
        border: 1px solid #b9c0ca;
        border-radius: 3px;
        padding: 1px 2px;
        font-size: 9pt;
        min-height: 22px;
    }
    QPushButton#tweakBtn:hover   { background: #d4e6f8; border-color: #2f6fae; }
    QPushButton#tweakBtn:pressed { background: #2f6fae; color: white; }

    /* ── Action (green-ish) buttons ── */
    QPushButton#actionBtn {
        background: #f0fdf4;
        border: 1px solid #86efac;
        border-radius: 4px;
        color: #14532d;
        padding: 3px 8px;
    }
    QPushButton#actionBtn:hover   { background: #dcfce7; border-color: #4ade80; }
    QPushButton#actionBtn:pressed { background: #16a34a; color: white; }

    QPushButton#greenBtn {
        background: #f0fdf4; border: 1px solid #86efac;
        color: #166534; border-radius: 4px;
    }
    QPushButton#greenBtn:hover   { background: #dcfce7; }
    QPushButton#greenBtn:pressed { background: #16a34a; color: white; }

    QPushButton#redBtn {
        background: #fef2f2; border: 1px solid #fca5a5;
        color: #991b1b; border-radius: 4px;
    }
    QPushButton#redBtn:hover   { background: #fee2e2; }
    QPushButton#redBtn:pressed { background: #dc2626; color: white; }

    QPushButton#captureBtn {
        background: #fef3c7; border: 1px solid #fbbf24;
        color: #92400e; border-radius: 4px; font-weight: bold; padding: 3px 10px;
    }
    QPushButton#captureBtn:hover   { background: #fde68a; border-color: #f59e0b; }
    QPushButton#captureBtn:pressed { background: #f59e0b; color: white; }

    /* ── Line edits ── */
    QLineEdit {
        background: #ffffff;
        border: 1px solid #b9c0ca;
        border-radius: 3px;
        padding: 2px 5px;
        selection-background-color: #2f6fae;
        selection-color: white;
    }
    QLineEdit:focus { border-color: #2f6fae; }

    /* ── Tab widget ── */
    QTabWidget::pane {
        border: 1px solid #c8ccd2;
        border-radius: 0 4px 4px 4px;
        background: #ffffff;
    }
    QTabBar::tab {
        background: #e6e9ef;
        border: 1px solid #c8ccd2;
        border-bottom: none;
        border-radius: 4px 4px 0 0;
        padding: 5px 16px;
        margin-right: 2px;
        color: #4a5568;
    }
    QTabBar::tab:selected {
        background: #ffffff;
        color: #1f4f7f;
        font-weight: bold;
        border-bottom: 2px solid #2f6fae;
    }
    QTabBar::tab:hover:!selected { background: #d4e6f8; color: #2f6fae; }

    /* ── Group boxes ── */
    QGroupBox {
        border: 1px solid #c8ccd2;
        border-radius: 5px;
        margin-top: 10px;
        padding-top: 6px;
        font-weight: bold;
        color: #28313f;
        background: #ffffff;
    }
    QGroupBox::title {
        subcontrol-origin: margin;
        subcontrol-position: top left;
        padding: 0 6px;
        left: 10px;
        color: #28313f;
    }

    /* ── List widget ── */
    QListWidget {
        background: #ffffff;
        border: 1px solid #c8ccd2;
        border-radius: 4px;
        alternate-background-color: #f7f9fb;
        outline: none;
    }
    QListWidget::item { padding: 3px 6px; }
    QListWidget::item:selected { background: #2f6fae; color: white; }
    QListWidget::item:hover:!selected { background: #edf4fd; }

    /* ── Check boxes ── */
    QCheckBox { spacing: 5px; background: transparent; }
    QCheckBox::indicator {
        width: 14px; height: 14px;
        border: 1px solid #b9c0ca;
        border-radius: 3px;
        background: white;
    }
    QCheckBox::indicator:checked {
        background: #2f6fae;
        border-color: #1f4f7f;
    }

    /* ── Scroll area ── */
    QScrollArea { border: none; background: transparent; }
    QScrollBar:vertical {
        background: #f3f4f6; width: 10px; margin: 0;
    }
    QScrollBar::handle:vertical {
        background: #b9c0ca; border-radius: 4px; min-height: 20px;
    }
    QScrollBar::handle:vertical:hover { background: #2f6fae; }
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }

    /* ── Vertical separators ── */
    QFrame[frameShape="5"] { color: #c8ccd2; max-width: 1px; }
    QFrame#toolSep { color: #c8ccd2; max-width: 1px; }

    /* ── Table widget ── */
    QTableWidget {
        background: #ffffff;
        border: 1px solid #c8ccd2;
        gridline-color: #e6e9ef;
        alternate-background-color: #f7f9fb;
        outline: none;
    }
    QTableWidget QHeaderView::section {
        background: #eceff3;
        border: none;
        border-right: 1px solid #c8ccd2;
        border-bottom: 1px solid #c8ccd2;
        padding: 3px 6px;
        font-weight: bold;
        color: #28313f;
    }
    QTableWidget::item:selected { background: #2f6fae; color: white; }

    /* ── Combo box ── */
    QComboBox {
        background: #ffffff;
        border: 1px solid #b9c0ca;
        border-radius: 3px;
        padding: 2px 6px;
        min-height: 22px;
    }
    QComboBox:focus { border-color: #2f6fae; }
    QComboBox::drop-down { border: none; width: 18px; }
    QComboBox QAbstractItemView {
        background: #ffffff;
        border: 1px solid #c8ccd2;
        selection-background-color: #2f6fae;
        selection-color: white;
    }

    /* ── Spin boxes ── */
    QSpinBox, QDoubleSpinBox {
        background: #ffffff;
        border: 1px solid #b9c0ca;
        border-radius: 3px;
        padding: 2px 5px;
    }
    QSpinBox:focus, QDoubleSpinBox:focus { border-color: #2f6fae; }

    /* ── Menu bar (light theme matching FrameByFrame) ── */
    QMenuBar {
        background: #eceff3;
        color: #20242a;
        padding: 2px 4px;
        font-size: 9pt;
        spacing: 2px;
        border-bottom: 1px solid #c8ccd2;
    }
    QMenuBar::item {
        padding: 4px 14px;
        border-radius: 4px;
        background: transparent;
    }
    QMenuBar::item:selected { background: #2f6fae; color: white; }
    QMenuBar::item:pressed  { background: #1f4f7f; color: white; }

    QMenu {
        background: #ffffff;
        border: 1px solid #c8ccd2;
        border-radius: 6px;
        padding: 4px;
        font-size: 9pt;
    }
    QMenu::item {
        padding: 5px 32px 5px 14px;
        border-radius: 3px;
        color: #20242a;
    }
    QMenu::item:selected  { background: #edf4fd; color: #1f4f7f; }
    QMenu::item:disabled  { color: #a0a8b4; }
    QMenu::separator      { height: 1px; background: #c8ccd2; margin: 3px 10px; }
    QMenu::right-arrow    { image: none; width: 8px; }
""")
