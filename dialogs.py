# dialogs.py — SetupDialog (settings window) and _CalibDialog (pixel calibration).
# Debug entry point: check SetupDialog.values() dict keys match DEFAULT_CONFIG keys.
# External deps: none.

from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QFrame, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
    QScrollArea, QVBoxLayout, QWidget,
)
from PyQt6.QtCore import Qt, QTimer

from position_models import DEFAULT_CONFIG


# ── Calibration dialog ─────────────────────────────────────────────────────────

class _CalibDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Camera Pixel Calibration")
        layout = QVBoxLayout(self)

        instr = QLabel(
            "1. Click 'Select First' then click a point on the camera image.\n"
            "2. Click 'Select Second' then click a second point.\n"
            "3. Enter the known distance between the two points and click Calibrate."
        )
        instr.setWordWrap(True)
        layout.addWidget(instr)

        r1 = QHBoxLayout()
        self.first_btn = QPushButton("Select First Point")
        self.first_lbl = QLabel("(not selected)")
        r1.addWidget(self.first_btn)
        r1.addWidget(self.first_lbl)
        layout.addLayout(r1)

        r2 = QHBoxLayout()
        self.second_btn = QPushButton("Select Second Point")
        self.second_lbl = QLabel("(not selected)")
        r2.addWidget(self.second_btn)
        r2.addWidget(self.second_lbl)
        layout.addLayout(r2)

        dr = QHBoxLayout()
        dr.addWidget(QLabel("Known distance (mm):"))
        self.dist_edit = QLineEdit()
        dr.addWidget(self.dist_edit)
        layout.addLayout(dr)

        br = QHBoxLayout()
        self.ok_btn = QPushButton("Calibrate")
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        br.addWidget(self.ok_btn)
        br.addWidget(cancel_btn)
        layout.addLayout(br)


# ── Setup dialog ───────────────────────────────────────────────────────────────

class SetupDialog(QDialog):
    """Standalone settings window opened from the Setup menu."""

    _SECTIONS = [
        ("motors", "Motor PVs", [
            ("X_MOTOR_PV",      "X Motor PV:"),
            ("Y_MOTOR_PV",      "Y Motor PV:"),
            ("Z_MOTOR_PV",      "Z Motor PV:"),
            ("X_MOTOR_NAME",    "X Axis Name:"),
            ("Y_MOTOR_NAME",    "Y Axis Name:"),
            ("Z_MOTOR_NAME",    "Z Axis Name:"),
        ]),
        ("camera", "Camera", [
            ("CAMERA_PREFIX",    "Camera Prefix:"),
            ("IMAGE_PREFIX",     "Image Prefix:"),
            ("ROI_PREFIX",       "ROI Prefix:"),
            ("AUTOFOCUS_STEP",   "Autofocus Step (mm):"),
            ("PVA_CHANNEL",      "PVA Channel (NTNDArray):"),
            ("PVA_HOST",         "PVA Host (blank = auto):"),
        ]),
    ]

    def __init__(self, cfg: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Setup — ASWAXS Sample Station")
        self.resize(520, 480)
        self.setModal(True)
        self._edits: dict[str, QLineEdit] = {}
        self._group_widgets: dict[str, QGroupBox] = {}
        self._build_ui(cfg)

    def _build_ui(self, cfg: dict):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 8)
        root.setSpacing(8)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget()
        v = QVBoxLayout(inner)
        v.setContentsMargins(0, 0, 6, 0)
        v.setSpacing(10)

        for group_key, title, fields in self._SECTIONS:
            box = QGroupBox(title)
            fl  = QFormLayout(box)
            fl.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
            fl.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
            for cfg_key, label in fields:
                edit = QLineEdit(str(cfg.get(cfg_key, "")))
                fl.addRow(label, edit)
                self._edits[cfg_key] = edit
            v.addWidget(box)
            self._group_widgets[group_key] = box

        v.addStretch()
        scroll.setWidget(inner)
        root.addWidget(scroll, 1)

        # Button box
        bbox = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel |
            QDialogButtonBox.StandardButton.RestoreDefaults,
        )
        bbox.accepted.connect(self.accept)
        bbox.rejected.connect(self.reject)
        bbox.button(QDialogButtonBox.StandardButton.RestoreDefaults).clicked.connect(
            self._restore_defaults)
        root.addWidget(bbox)

    def _restore_defaults(self):
        reply = QMessageBox.question(self, "Reset",
            "Reset all fields to factory defaults?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            for key, edit in self._edits.items():
                edit.setText(str(DEFAULT_CONFIG.get(key, "")))

    def scroll_to(self, group_key: str):
        box = self._group_widgets.get(group_key)
        if box:
            # Defer so the dialog has finished laying out
            QTimer.singleShot(0, lambda: box.setFocus())

    def values(self) -> dict:
        return {key: edit.text().strip() for key, edit in self._edits.items()}
