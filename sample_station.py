"""
sample_station.py — ASWAXS Sample Station
Combines live camera/motor control with rich sample position planning.
"""
# sample_station.py — SampleStation main window: camera, motors, menus, and wiring.
# Debug entry point: check _apply_config() → _apply_camera_config() for PV connection issues.
# External deps: CAMERA_PREFIX+ArrayData/ArraySizeX_RBV/ArraySizeY_RBV/DetectorState_RBV/Acquire,
#                ROI_PREFIX+MinX/MinY/SizeX/SizeY.

import os
import sys
import json
import time
import atexit

import numpy as np
import pyqtgraph as pg

try:
    import cv2
    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QGroupBox,
    QLabel, QLineEdit, QPushButton, QCheckBox, QTabWidget,
    QFileDialog, QMessageBox, QSplitter, QApplication,
    QAbstractItemView, QDialog, QDialogButtonBox, QFrame,
    QScrollArea, QTableWidget, QTableWidgetItem, QComboBox,
    QSpinBox, QDoubleSpinBox, QFormLayout, QSizePolicy,
    QMainWindow, QMenu,
)
from PyQt6.QtCore import (
    Qt, pyqtSignal, pyqtSlot, QObject, QEvent,
    QItemSelection, QItemSelectionModel, QRectF,
    QThread, QTimer,
)
from PyQt6.QtGui import QFont, QPainter, QColor, QPen, QBrush, QKeySequence, QShortcut

pg.setConfigOption('imageAxisOrder', 'row-major')

try:
    import epics
    from epics import Motor, PV
    import epics.ca as _ca
    _ca.initialize_libca()
    EPICS_AVAILABLE = True
except Exception as _epics_err:
    EPICS_AVAILABLE = False
    epics = None  # type: ignore[assignment]
    Motor = None
    PV = None
    print(f"Warning: EPICS unavailable ({_epics_err}) — running in offline mode")

# ── Module-level path constants ────────────────────────────────────────────────

_DIR = os.path.dirname(os.path.abspath(__file__))

# When run directly (`python sample_station.py`) __file__ is the software dir — use it.
# When run as a pip entry point (`aswaxs-station`) __file__ is in site-packages and the
# config will not be there; fall back to the current working directory instead, so the
# user can cd to the software directory and run `aswaxs-station` from there.
_script_cfg = os.path.join(_DIR, "sample_station_config.json")
_cwd_cfg    = os.path.join(os.getcwd(), "sample_station_config.json")
CONFIG_FILE = _script_cfg if os.path.exists(_script_cfg) else _cwd_cfg

_CONFIG_DIR = os.path.dirname(CONFIG_FILE)
CALIB_FILE  = os.path.join(_CONFIG_DIR, "Data", "camera_calib.txt")

# ── Local module imports ───────────────────────────────────────────────────────

from epics_bridge import _PVBridge
from frame_decoder import _decode_frame
from position_models import DEFAULT_CONFIG
from position_io import normalize_positions, blank_position
from motor_panel import MotorPanel
from autofocus_worker import _AutofocusWorker
from dialogs import SetupDialog, _CalibDialog
from position_tab import SamplePositionTab
from styles import apply_style


# ── Main station widget ────────────────────────────────────────────────────────

class SampleStation(QMainWindow):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("ASWAXS Sample Station")

        # Config
        self.cfg = self._load_config()

        # Camera / calibration state
        self.cf            = 0.002450
        self.positions: list[dict] = []
        self.calibration_flag = False
        self.calib_chosen  = 1
        self.calib_pos     = [[0, 0], [1, 1]]
        self.pos1          = [0, 0]
        self.pos2          = [1, 1]
        self.image: np.ndarray | None = None
        self.image_width   = 1280
        self.image_height        = 960
        self.image_height_sensor = 960   # from ArraySizeY_RBV
        self.image_cx      = 640
        self.image_cy      = 480
        self.cursor_x      = 0
        self.cursor_y      = 0
        self.beam_x        = 640
        self.beam_y        = 480
        self._acquire_pv: PV | None = None
        self._pva_thread = None
        self._center_initialized = False
        self._last_frame_time  = 0.0
        self._cam_fps_limit    = 15        # max display frames per second

        # ROI overlay PV state
        self._roi_pvs:     dict = {}
        self._roi_bridges: dict = {}
        self._roi_vals           = {'MinX': 0, 'MinY': 0, 'SizeX': 50, 'SizeY': 50}

        # Autofocus thread (created fresh per run)
        self._af_thread: QThread | None = None
        self._af_worker: _AutofocusWorker | None = None

        self._build_ui()
        self._apply_style()
        self._init_overlays()
        self._load_calib_file()   # must come after _init_overlays (sets crosshair lines)
        self._apply_config()

    # ── Config ─────────────────────────────────────────────────────────────

    def _load_config(self) -> dict:
        cfg = dict(DEFAULT_CONFIG)
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE) as f:
                    cfg.update(json.load(f))
            except Exception as e:
                print(f"Config load error: {e}")
        return cfg

    def _save_config(self):
        try:
            with open(CONFIG_FILE, 'w') as f:
                json.dump(self.cfg, f, indent=2)
            QMessageBox.information(self, "Saved", "Configuration saved to file.")
        except Exception as e:
            QMessageBox.warning(self, "Config Error", f"Could not save:\n{e}")

    def _load_config_action(self):
        self.cfg = self._load_config()
        self._apply_config()
        QMessageBox.information(self, "Config Loaded",
                                f"Configuration loaded from:\n{CONFIG_FILE}")

    # ── UI construction ────────────────────────────────────────────────────

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        v = QVBoxLayout(central)
        v.setContentsMargins(6, 6, 6, 6)
        v.setSpacing(4)
        v.addWidget(self._build_motor_bar())
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_camera_tab(),    "Camera")
        self.tabs.addTab(self._build_positions_tab(), "Sample Positions")
        v.addWidget(self.tabs, 1)
        self._build_menu_bar()
        self._connect_signals()

    def _build_menu_bar(self):
        mb = self.menuBar()

        # ── File ────────────────────────────────────────────────────────────
        fm = mb.addMenu("&File")
        fm.addAction("New Positions",                 lambda: self.pos_tab._new())
        fm.addAction("Open Positions…",              lambda: self.pos_tab._open())
        fm.addAction("Save Positions",               lambda: self.pos_tab._save())
        fm.addAction("Save Positions As…",           lambda: self.pos_tab._save_as())
        fm.addAction("Save Positions As… (Split CSV)", lambda: self.pos_tab._save_as_split())
        fm.addSeparator()
        ex = fm.addMenu("Export")
        ex.addAction("Bluesky CSV…",         lambda: self.pos_tab._export_bluesky())
        ex.addAction("Bluesky CSV (Split)…", lambda: self.pos_tab._export_bluesky_split())
        ex.addAction("Reducer Pairs CSV…",   lambda: self.pos_tab._export_reducer())
        fm.addSeparator()
        fm.addAction("Exit",                 self.close)

        # ── Acquisition ─────────────────────────────────────────────────────
        am = mb.addMenu("&Acquisition")
        am.addAction("▶  Start Camera",      self._start_camera)
        am.addAction("■  Stop Camera",       self._stop_camera)
        am.addSeparator()
        am.addAction("Autofocus",            self._run_autofocus)
        am.addAction("Calibrate Camera…",    self._open_calib_dialog)

        # ── Positions ────────────────────────────────────────────────────────
        pm = mb.addMenu("&Positions")
        pm.addAction("📍 Capture from Stage", lambda: self.pos_tab._capture_from_stage())
        pm.addSeparator()
        pm.addAction("Add Row",              lambda: self.pos_tab._add_row())
        pm.addAction("Delete Selected",      lambda: self.pos_tab._delete_selected())
        pm.addAction("Duplicate",            lambda: self.pos_tab._duplicate_selected())
        pm.addAction("Move Up",              lambda: self.pos_tab._move_selected(-1))
        pm.addAction("Move Down",            lambda: self.pos_tab._move_selected(1))
        pm.addSeparator()
        tm = pm.addMenu("Templates")
        tm.addAction("Freeform",             lambda: self.pos_tab.set_positions([blank_position(i) for i in range(5)]))
        tm.addAction("Capillary Linear…",    lambda: self.pos_tab._capillary_dialog())

        def _open_rack_builder():
            from position_rack_builder import RackBuilderDialog
            positions = self.pos_tab._positions if self.pos_tab._positions else None
            dlg = RackBuilderDialog(positions, parent=self)
            if dlg.exec() == QDialog.DialogCode.Accepted:
                self.pos_tab.set_positions(dlg.result_positions)

        tm.addAction("Rack Builder…",        _open_rack_builder)
        tm.addAction("Chip Manual Map",      lambda: self.pos_tab.set_positions([blank_position(i, layout="chip") for i in range(10)]))

        # ── Setup ────────────────────────────────────────────────────────────
        sm = mb.addMenu("&Setup")
        sm.addAction("Open Setup…",          self._open_setup_dialog)
        sm.addSeparator()
        sm.addAction("Load Config",          self._load_config_action)
        sm.addAction("Save Config",          self._save_config)
        sm.addAction("Reset to Defaults",    self._reset_config)

        # ── Help ─────────────────────────────────────────────────────────────
        hm = mb.addMenu("&Help")
        hm.addAction("About",               self._show_about)

    def _open_calib_dialog(self):
        dlg = _CalibDialog(self.cf, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.cf = dlg.factor()
            if hasattr(self, 'cf_edit'):
                self.cf_edit.setText(f"{self.cf:.6f}")

    def _open_setup_dialog(self, focus_group: str | None = None):
        dlg = SetupDialog(self.cfg, self)
        dlg.setWindowTitle("Setup — ASWAXS Sample Station")
        if focus_group:
            dlg.scroll_to(focus_group)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.cfg.update(dlg.values())
            self._save_config()
            self._apply_config()

    def _reset_config(self):
        reply = QMessageBox.question(self, "Reset Config",
            "Reset all settings to factory defaults?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return
        self.cfg = dict(DEFAULT_CONFIG)
        self._apply_config()
        QMessageBox.information(self, "Reset", "Settings reset to defaults and applied.")

    def _show_about(self):
        QMessageBox.about(self, "About ASWAXS Sample Station",
            "<b>ASWAXS Sample Station</b><br>"
            "Version 1.0<br><br>"
            "Combines live camera/motor control with rich sample position planning.<br><br>"
            "Features:<br>"
            "• EPICS motor control (X/Y/Z)<br>"
            "• Live camera with click-to-move<br>"
            "• Rich position management (name, role, group, solvent)<br>"
            "• Role-colored position map<br>"
            "• Capillary rack builder<br>"
            "• Catmull-Rom path interpolation<br>"
            "• Export to Bluesky CSV and Reducer Pairs CSV")

    def _build_motor_bar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("motorBar")
        outer = QVBoxLayout(bar)
        outer.setContentsMargins(8, 6, 8, 6)
        outer.setSpacing(0)

        self.x_motor  = MotorPanel("X")
        self.y_motor  = MotorPanel("Y")
        self.z_motor  = MotorPanel("Z (Focus)")
        self.bx_motor = MotorPanel("Beam X")
        self.by_motor = MotorPanel("Beam Y")

        # Single shared grid — all motor sub-widgets share column definitions
        # Columns 0-12: X/Y/Z motors; 13: stretch; 14: right-side extras
        # Column 15: vertical separator; columns 16-28: Beam X/Y; 29: stretch
        grid_w = QWidget()
        grid = QGridLayout(grid_w)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(4)
        grid.setVerticalSpacing(0)
        grid.setColumnStretch(13, 1)   # spacer between sample-motor cols and right extras
        grid.setColumnStretch(29, 1)   # spacer after beam-motor cols

        # Motor rows at grid rows 0, 2, 4; separators at 1, 3
        self.x_motor.place_in_grid(grid, 0)
        self.y_motor.place_in_grid(grid, 2)
        self.z_motor.place_in_grid(grid, 4)

        # Vertical separator between sample motors and beam motors
        sep_v = QFrame()
        sep_v.setFrameShape(QFrame.Shape.VLine)
        sep_v.setObjectName("motorSep")
        grid.addWidget(sep_v, 0, 15, 5, 1)  # span all 5 rows

        # Beam motors at col_offset=16; rows 0 and 2 (with separator at row 1)
        self.bx_motor.place_in_grid(grid, 0, col_offset=16)
        self.by_motor.place_in_grid(grid, 2, col_offset=16)

        sep0 = QFrame(); sep0.setFrameShape(QFrame.Shape.HLine); sep0.setObjectName("motorSep")
        sep1 = QFrame(); sep1.setFrameShape(QFrame.Shape.HLine); sep1.setObjectName("motorSep")
        # HLine separators span sample-motor columns only (0–14); beam motors have their own
        grid.addWidget(sep0, 1, 0, 1, 15)
        grid.addWidget(sep1, 3, 0, 1, 15)

        # Beam-motor HLine separator between Beam X and Beam Y
        sep_b = QFrame(); sep_b.setFrameShape(QFrame.Shape.HLine); sep_b.setObjectName("motorSep")
        grid.addWidget(sep_b, 1, 16, 1, 13)

        # Right-side extras in column 14
        # X row: ROI
        roi_w = QWidget()
        roi_h = QHBoxLayout(roi_w)
        roi_h.setContentsMargins(0, 0, 0, 0)
        roi_h.setSpacing(4)
        grid.addWidget(roi_w, 0, 14)

        outer.addWidget(grid_w)
        return bar

    def _build_camera_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(4, 4, 4, 4)

        # Top toolbar
        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)

        toolbar.addWidget(QLabel("Cal. Factor:"))
        self.cf_edit = QLineEdit()
        self.cf_edit.setFixedWidth(88)
        toolbar.addWidget(self.cf_edit)
        self.calibrate_btn = QPushButton("Calibrate")
        toolbar.addWidget(self.calibrate_btn)

        sep0 = QFrame()
        sep0.setFrameShape(QFrame.Shape.VLine)
        sep0.setFrameShadow(QFrame.Shadow.Sunken)
        toolbar.addWidget(sep0)

        self.click_move_cb = QCheckBox("DoubleClick Move")
        self.click_move_cb.setChecked(True)
        toolbar.addWidget(self.click_move_cb)
        self.auto_add_cb = QCheckBox("Auto Add2List")
        toolbar.addWidget(self.auto_add_cb)
        self.add_pos_btn = QPushButton("Add Position")
        self.add_pos_btn.setToolTip("Add current motor position to the list")
        toolbar.addWidget(self.add_pos_btn)

        sep1 = QFrame()
        sep1.setFrameShape(QFrame.Shape.VLine)
        sep1.setFrameShadow(QFrame.Shadow.Sunken)
        toolbar.addWidget(sep1)

        # Focus section — hidden for now, kept for future use
        self.focus_lbl = QLabel("—")
        self.focus_lbl.setObjectName("focusLabel")
        self.autofocus_btn = QPushButton("Autofocus")
        self.af_cancel_btn = QPushButton("Cancel AF")
        self.af_cancel_btn.setObjectName("cancelBtn")
        for _fw in (self.focus_lbl, self.autofocus_btn, self.af_cancel_btn):
            _fw.setVisible(False)

        self.cam_start_btn = QPushButton("▶ Start")
        toolbar.addWidget(self.cam_start_btn)
        self.cam_stop_btn = QPushButton("■ Stop")
        toolbar.addWidget(self.cam_stop_btn)
        self.cam_state_lbl = QLabel("Camera: —")
        self.cam_state_lbl.setObjectName("camStateLabel")
        toolbar.addWidget(self.cam_state_lbl)

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.VLine)
        sep2.setFrameShadow(QFrame.Shadow.Sunken)
        toolbar.addWidget(sep2)

        toolbar.addWidget(QLabel("Exp (s):"))
        self.acqtime_edit = QLineEdit()
        self.acqtime_edit.setFixedWidth(60)
        self.acqtime_edit.setToolTip("AcquireTime — exposure per frame (s)")
        toolbar.addWidget(self.acqtime_edit)

        toolbar.addWidget(QLabel("Period (s):"))
        self.acqperiod_edit = QLineEdit()
        self.acqperiod_edit.setFixedWidth(60)
        self.acqperiod_edit.setToolTip("AcquirePeriod — time between frame starts (s)")
        toolbar.addWidget(self.acqperiod_edit)

        toolbar.addWidget(QLabel("Mode:"))
        self.imgmode_combo = QComboBox()
        self.imgmode_combo.addItems(["Single", "Multiple", "Continuous"])
        self.imgmode_combo.setFixedWidth(100)
        self.imgmode_combo.setToolTip("ImageMode — detector acquisition mode")
        toolbar.addWidget(self.imgmode_combo)

        toolbar.addStretch()
        v.addLayout(toolbar)

        # ROI control row
        roi_bar = QHBoxLayout()
        roi_bar.setSpacing(6)
        roi_bar.addWidget(QLabel("ROI:"))
        roi_bar.addWidget(QLabel("X:"))
        self.roi_x_spin = QSpinBox()
        self.roi_x_spin.setRange(0, 9999)
        self.roi_x_spin.setFixedWidth(65)
        roi_bar.addWidget(self.roi_x_spin)
        roi_bar.addWidget(QLabel("Y:"))
        self.roi_y_spin = QSpinBox()
        self.roi_y_spin.setRange(0, 9999)
        self.roi_y_spin.setFixedWidth(65)
        roi_bar.addWidget(self.roi_y_spin)
        roi_bar.addWidget(QLabel("Size:"))
        self.roi_size_spin = QSpinBox()
        self.roi_size_spin.setRange(1, 9999)
        self.roi_size_spin.setFixedWidth(65)
        roi_bar.addWidget(self.roi_size_spin)
        self.roi_center_btn = QPushButton("Center ROI")
        self.roi_center_btn.setFixedWidth(90)
        self.roi_center_btn.setToolTip("Move ROI to image center")
        roi_bar.addWidget(self.roi_center_btn)
        roi_bar.addStretch()
        v.addLayout(roi_bar)

        # Camera image
        self.gfx = pg.GraphicsLayoutWidget()
        self.view_box = self.gfx.addViewBox(row=0, col=0)
        self.view_box.setAspectLocked(True)
        self.view_box.invertY(True)
        self.image_item = pg.ImageItem()
        self.view_box.addItem(self.image_item)
        v.addWidget(self.gfx, stretch=1)

        # Bottom status bar
        status = QHBoxLayout()
        self.cursor_lbl = QLabel("X=0, Y=0, I=0")
        self.cursor_lbl.setObjectName("statusLabel")
        status.addWidget(self.cursor_lbl)
        status.addStretch()
        self.select_beam_cb = QCheckBox("Select Beam Position")
        status.addWidget(self.select_beam_cb)
        self.beam_lbl = QLabel("BeamX=—, BeamY=—")
        self.beam_lbl.setObjectName("statusLabel")
        status.addWidget(self.beam_lbl)
        v.addLayout(status)

        return w

    def _build_positions_tab(self) -> QWidget:
        self.pos_tab = SamplePositionTab(station=self)
        return self.pos_tab

    # _build_setup_tab removed — setup is now SetupDialog (see dialogs.py)

    # ── Signal wiring ──────────────────────────────────────────────────────

    def _connect_signals(self):
        # Motor bar
        self.roi_x_spin.editingFinished.connect(self._on_roi_spin_changed)
        self.roi_y_spin.editingFinished.connect(self._on_roi_spin_changed)
        self.roi_size_spin.editingFinished.connect(self._on_roi_spin_changed)
        self.roi_center_btn.clicked.connect(self._on_roi_center)

        # Camera tab
        self.cf_edit.returnPressed.connect(self.cfChanged)
        self.add_pos_btn.clicked.connect(self.addPosition)
        self.calibrate_btn.clicked.connect(self.openCalibration)
        self.autofocus_btn.clicked.connect(self._run_autofocus)
        self.af_cancel_btn.clicked.connect(self._cancel_autofocus)
        self.cam_start_btn.clicked.connect(self._start_camera)
        self.cam_stop_btn.clicked.connect(self._stop_camera)
        self.acqtime_edit.returnPressed.connect(self._send_acqtime)
        self.acqperiod_edit.returnPressed.connect(self._send_acqperiod)
        self.imgmode_combo.currentIndexChanged.connect(self._send_imgmode)

        # Camera mouse events
        scene = self.view_box.scene()
        scene.sigMouseMoved.connect(self._update_cursor_label)
        scene.sigMouseClicked.connect(self._on_camera_click)

    # ── EPICS connection ───────────────────────────────────────────────────

    def _apply_config(self):
        # cfg is already up-to-date (SetupDialog.values() was merged before calling this)
        self._apply_motor_config()
        self._apply_camera_config()
        self._apply_roi_config()

    def _apply_motor_config(self):
        self.x_motor.connect(self.cfg["X_MOTOR_PV"])
        self.y_motor.connect(self.cfg["Y_MOTOR_PV"])
        self.z_motor.connect(self.cfg["Z_MOTOR_PV"])
        self.bx_motor.connect(self.cfg.get("BX_MOTOR_PV", ""))
        self.by_motor.connect(self.cfg.get("BY_MOTOR_PV", ""))
        self.pos_tab.set_axis_names(
            self.cfg.get("X_MOTOR_NAME", "s_x"),
            self.cfg.get("Y_MOTOR_NAME", "s_y"),
            self.cfg.get("Z_MOTOR_NAME", "s_z"),
        )

    def _apply_camera_config(self):
        # Stop any pending connection timeout
        if hasattr(self, '_cam_conn_timer'):
            self._cam_conn_timer.stop()

        # Stop existing PVA thread if running
        self._stop_pva_thread()

        for attr in ('_img_pv', '_wid_pv', '_hgt_pv', '_state_pv',
                     '_acqtime_pv', '_acqperiod_pv', '_imgmode_pv'):
            old = getattr(self, attr, None)
            if old is not None:
                try:
                    old.disconnect()
                except Exception:
                    pass

        if not EPICS_AVAILABLE:
            if hasattr(self, 'cam_state_lbl'):
                self.cam_state_lbl.setText("● Camera: offline (no EPICS)")
                self.cam_state_lbl.setStyleSheet("color: #9ca3af; font-size: 8.5pt;")
            return

        cam     = self.cfg.get("CAMERA_PREFIX", "").strip()
        img     = self.cfg.get("IMAGE_PREFIX",  "").strip()
        pva_ch  = self.cfg.get("PVA_CHANNEL",   "").strip()
        pva_host = self.cfg.get("PVA_HOST",     "").strip()

        if not cam:
            self.cam_state_lbl.setText("● Camera: prefix not configured")
            self.cam_state_lbl.setStyleSheet("color: #dc2626; font-size: 8.5pt;")
            return

        try:
            self._state_bridge = _PVBridge()
            self._acq_bridge   = _PVBridge()
            self._state_bridge.changed.connect(self._on_cam_state)
            self._state_bridge.conn_state.connect(self._on_cam_conn)
            self._acq_bridge.conn_state.connect(self._on_acquire_conn)

            self._state_pv = PV(cam + "DetectorState_RBV",
                                callback=self._state_bridge,
                                connection_callback=self._state_bridge.conn_cb,
                                auto_monitor=True)

            if self._acquire_pv is not None:
                try:
                    self._acquire_pv.disconnect()
                except Exception:
                    pass
            self._acquire_pv = PV(cam + "Acquire",
                                  connection_callback=self._acq_bridge.conn_cb)
            atexit.register(self._stop_acquire)
            self._acquire_pv.put(1)

            if pva_ch:
                # ── PVAccess path ──────────────────────────────────────────
                self._start_pva_thread(pva_ch, pva_host)
                self.cam_state_lbl.setText("● Camera: PVA connecting…")
                self.cam_state_lbl.setStyleSheet("color: #d97706; font-size: 8.5pt;")
            else:
                # ── Channel Access path ────────────────────────────────────
                if not img:
                    self.cam_state_lbl.setText("● Camera: IMAGE_PREFIX not configured")
                    self.cam_state_lbl.setStyleSheet("color: #dc2626; font-size: 8.5pt;")
                    return
                self._img_bridge = _PVBridge()
                self._wid_bridge = _PVBridge()
                self._hgt_bridge = _PVBridge()
                self._img_bridge.changed.connect(self._on_image_data)
                self._wid_bridge.changed.connect(self._on_width_data)
                self._hgt_bridge.changed.connect(self._on_height_data)
                self._img_pv = PV(img + "ArrayData",
                                  callback=self._img_bridge, auto_monitor=True)
                self._wid_pv = PV(cam + "ArraySizeX_RBV",
                                  callback=self._wid_bridge, auto_monitor=True)
                self._hgt_pv = PV(cam + "ArraySizeY_RBV",
                                  callback=self._hgt_bridge, auto_monitor=True)
                self.cam_state_lbl.setText("● Camera: connecting…")
                self.cam_state_lbl.setStyleSheet("color: #d97706; font-size: 8.5pt;")

            self._cam_conn_timer = QTimer(self)
            self._cam_conn_timer.setSingleShot(True)
            self._cam_conn_timer.timeout.connect(self._on_cam_conn_timeout)
            self._cam_conn_timer.start(5000)

            # ── Acquisition settings PVs ───────────────────────────────────
            self._acqtime_bridge   = _PVBridge()
            self._acqperiod_bridge = _PVBridge()
            self._imgmode_bridge   = _PVBridge()
            self._acqtime_bridge.changed.connect(self._on_acqtime)
            self._acqperiod_bridge.changed.connect(self._on_acqperiod)
            self._imgmode_bridge.changed.connect(self._on_imgmode)
            self._acqtime_pv   = PV(cam + "AcquireTime",
                                    callback=self._acqtime_bridge, auto_monitor=True)
            self._acqperiod_pv = PV(cam + "AcquirePeriod",
                                    callback=self._acqperiod_bridge, auto_monitor=True)
            self._imgmode_pv   = PV(cam + "ImageMode",
                                    callback=self._imgmode_bridge, auto_monitor=True)

        except Exception as e:
            print(f"Camera PV setup error: {e}")
            self.cam_state_lbl.setText(f"● Camera: error — {e}")
            self.cam_state_lbl.setStyleSheet("color: #dc2626; font-size: 8.5pt;")

    def _start_pva_thread(self, channel: str, host: str = ""):
        from pva_monitor import PVAMonitorThread
        self._pva_thread = PVAMonitorThread(
            channel, pva_host=host, fps_limit=self._cam_fps_limit, parent=self
        )
        self._pva_thread.frame_ready.connect(self._on_pva_frame)
        self._pva_thread.connection_changed.connect(self._on_pva_conn)
        self._pva_thread.error_occurred.connect(self._on_pva_error)
        self._pva_thread.start()

    def _stop_pva_thread(self):
        t = getattr(self, '_pva_thread', None)
        if t is not None:
            t.stop()
            self._pva_thread = None

    @pyqtSlot(str, bool)
    def _on_cam_conn(self, _pvname: str, conn: bool):
        """DetectorState_RBV connection callback — fires when EPICS actually connects."""
        if conn:
            # Cancel the timeout — we have a live PV
            if hasattr(self, '_cam_conn_timer'):
                self._cam_conn_timer.stop()
        else:
            self.cam_state_lbl.setText("● Camera: disconnected")
            self.cam_state_lbl.setStyleSheet("color: #dc2626; font-size: 8.5pt;")

    @pyqtSlot(str, bool)
    def _on_acquire_conn(self, _pvname: str, conn: bool):
        """Start acquiring only once the Acquire PV has actually connected."""
        if conn and self._acquire_pv is not None:
            self._acquire_pv.put(1)

    def _on_cam_conn_timeout(self):
        """Fired 5 s after _apply_camera_config if camera PV never responded."""
        connected = self._state_pv is not None and getattr(self._state_pv, 'connected', False)
        if not connected:
            cam = self.cfg.get("CAMERA_PREFIX", "?")
            self.cam_state_lbl.setText("● Camera: no response")
            self.cam_state_lbl.setStyleSheet("color: #dc2626; font-size: 8.5pt;")
            self.cam_state_lbl.setToolTip(
                f"PV '{cam}DetectorState_RBV' did not connect after 5 s.\n"
                f"Check CAMERA_PREFIX in Setup."
            )

    def _stop_acquire(self):
        if self._acquire_pv:
            try:
                self._acquire_pv.put(0)
            except Exception:
                pass

    # ── Camera PV callbacks ────────────────────────────────────────────────

    @pyqtSlot(str, object)
    def _on_width_data(self, _pvname: str, value):
        self.image_width = int(value)
        self._update_center_from_dims()

    @pyqtSlot(str, object)
    def _on_height_data(self, _pvname: str, value):
        self.image_height_sensor = int(value)
        self.image_height = int(value)
        self._update_center_from_dims()

    def _update_center_from_dims(self):
        """Reposition the crosshair to the true image center as soon as dimensions are known."""
        if self.image_width > 0 and self.image_height > 0:
            self.image_cx = self.image_width  // 2
            self.image_cy = self.image_height // 2
            self._center_x_line.setValue(self.image_cx)
            self._center_y_line.setValue(self.image_cy)

    @pyqtSlot(str, object)
    def _on_cam_state(self, _pvname: str, value):
        text = str(value) if value is not None else "—"
        self.cam_state_lbl.setText(f"● Camera: {text}")
        low = text.lower()
        if any(k in low for k in ("acquire", "idle", "wait")):
            color = "#16a34a"   # green — live
        elif any(k in low for k in ("error", "abort", "fault", "disconnect")):
            color = "#dc2626"   # red — problem
        else:
            color = "#d97706"   # amber — intermediate state
        self.cam_state_lbl.setStyleSheet(f"color: {color}; font-size: 8.5pt;")

    @pyqtSlot(str, object)
    def _on_image_data(self, _pvname: str, value):
        now = time.monotonic()
        if now - self._last_frame_time < 1.0 / self._cam_fps_limit:
            return
        self._last_frame_time = now
        try:
            rgb, gray = _decode_frame(value, self.image_width)
            h, w = gray.shape
            self.image        = gray
            self.image_height = h
            self.image_item.setImage(gray, autoLevels=False, levels=(0, 255))
            if not self._center_initialized:
                self.view_box.autoRange()
                self.image_cx = w // 2
                self.image_cy = h // 2
                self._center_x_line.setValue(w / 2)
                self._center_y_line.setValue(h / 2)
                self._center_initialized = True
            if CV2_AVAILABLE:
                fp = float(cv2.Laplacian(gray, cv2.CV_64F).var())
                self.focus_lbl.setText(f"{fp:.3f}")
            else:
                self.focus_lbl.setText("(cv2 N/A)")
        except Exception as e:
            print(f"[camera] {e}")

    @pyqtSlot(object)
    def _on_pva_frame(self, arr):
        now = time.monotonic()
        if now - self._last_frame_time < 1.0 / self._cam_fps_limit:
            return
        self._last_frame_time = now
        try:
            if arr.ndim == 2:
                h, w = arr.shape
                if arr.dtype != np.uint8:
                    lo, hi = int(arr.min()), int(arr.max())
                    if hi > lo:
                        gray = ((arr.astype(np.float32) - lo) * (255.0 / (hi - lo))).astype(np.uint8)
                    else:
                        gray = np.zeros((h, w), dtype=np.uint8)
                else:
                    gray = arr
            elif arr.ndim == 3:
                h, w = arr.shape[:2]
                if CV2_AVAILABLE:
                    gray = cv2.cvtColor(arr, cv2.COLOR_BGR2GRAY)
                else:
                    gray = arr.mean(axis=2).astype(np.uint8)
            else:
                return
            self.image        = gray
            self.image_width  = w
            self.image_height = h
            self.image_item.setImage(gray, autoLevels=False, levels=(0, 255))
            if not self._center_initialized:
                self.view_box.autoRange()
                self.image_cx = w // 2
                self.image_cy = h // 2
                self._center_x_line.setValue(w / 2)
                self._center_y_line.setValue(h / 2)
                self._center_initialized = True
            if CV2_AVAILABLE:
                fp = float(cv2.Laplacian(gray, cv2.CV_64F).var())
                self.focus_lbl.setText(f"{fp:.3f}")
            else:
                self.focus_lbl.setText("(cv2 N/A)")
        except Exception as e:
            print(f"[pva frame] {e}")

    @pyqtSlot(bool)
    def _on_pva_conn(self, connected: bool):
        if connected:
            if hasattr(self, '_cam_conn_timer'):
                self._cam_conn_timer.stop()
            self.cam_state_lbl.setText("● Camera: PVA live")
            self.cam_state_lbl.setStyleSheet("color: #16a34a; font-size: 8.5pt;")
        else:
            self.cam_state_lbl.setText("● Camera: PVA disconnected")
            self.cam_state_lbl.setStyleSheet("color: #dc2626; font-size: 8.5pt;")

    @pyqtSlot(str)
    def _on_pva_error(self, msg: str):
        print(f"[pva] {msg}")
        self.cam_state_lbl.setText(f"● Camera: PVA error — {msg}")
        self.cam_state_lbl.setStyleSheet("color: #dc2626; font-size: 8.5pt;")

    # ── ROI overlay ────────────────────────────────────────────────────────────

    def _apply_roi_config(self):
        """Subscribe to ROI PVs and read initial values."""
        if not EPICS_AVAILABLE:
            return
        roi_prefix = self.cfg.get("ROI_PREFIX", "").strip()
        if not roi_prefix:
            return

        for pv in self._roi_pvs.values():
            try:
                pv.disconnect()
            except Exception:
                pass
        self._roi_pvs.clear()
        self._roi_bridges.clear()

        for key in ('MinX', 'MinY', 'SizeX', 'SizeY'):
            bridge = _PVBridge()
            bridge.changed.connect(
                lambda _n, val, k=key: self._on_roi_pv(k, val)
            )
            self._roi_bridges[key] = bridge
            self._roi_pvs[key] = PV(roi_prefix + key,
                                    callback=bridge, auto_monitor=True)

        # Read current values immediately to position the overlay
        for key in ('MinX', 'MinY', 'SizeX', 'SizeY'):
            v = epics.caget(roi_prefix + key)
            if v is not None:
                self._roi_vals[key] = int(v)
        self._update_roi_rect()

    def _on_roi_pv(self, key: str, value):
        """Slot: a ROI PV changed — update the overlay rectangle."""
        try:
            v = int(value)
            if self._roi_vals.get(key) == v:
                return  # echo-back of our own caput — ignore
            self._roi_vals[key] = v
            self._update_roi_rect()
        except Exception:
            pass

    def _update_roi_rect(self):
        """Move/resize the RectROI and spinboxes to match _roi_vals (no PV write)."""
        v = self._roi_vals
        sx = v['SizeX']
        sy = v['SizeY']
        self._roi_rect.blockSignals(True)
        self._roi_rect.setPos([v['MinX'], v['MinY']])
        self._roi_rect.setSize([sx, sy])
        self._roi_rect.blockSignals(False)
        for spin, val in ((self.roi_x_spin, v['MinX']),
                          (self.roi_y_spin, v['MinY']),
                          (self.roi_size_spin, sx)):
            spin.blockSignals(True)
            spin.setValue(val)
            spin.blockSignals(False)

    @pyqtSlot(object)
    def _on_roi_dragged(self, _roi):
        """Slot: user finished dragging/resizing the ROI — write back to EPICS."""
        if not EPICS_AVAILABLE:
            return
        roi_prefix = self.cfg.get("ROI_PREFIX", "").strip()
        if not roi_prefix:
            return
        pos  = self._roi_rect.pos()
        size = self._roi_rect.size()
        x, y = max(0, int(pos.x())), max(0, int(pos.y()))
        s = max(1, int(size.x()), int(size.y()))
        self._roi_vals.update({'MinX': x, 'MinY': y, 'SizeX': s, 'SizeY': s})
        self._update_roi_rect()
        epics.caput(roi_prefix + 'MinX',  x)
        epics.caput(roi_prefix + 'MinY',  y)
        epics.caput(roi_prefix + 'SizeX', s)
        epics.caput(roi_prefix + 'SizeY', s)

    # ── Overlay graphics ───────────────────────────────────────────────────

    def _init_overlays(self):
        w = self.image_width
        self._center_x_line = pg.InfiniteLine(pos=w / 2, angle=90, pen=pg.mkPen('r', width=1), movable=False)
        self._center_y_line = pg.InfiniteLine(pos=0,     angle=0,  pen=pg.mkPen('r', width=1), movable=False)
        self._cursor_x_line = pg.InfiniteLine(pos=0,     angle=90, pen=pg.mkPen('b', width=1), movable=False)
        self._cursor_y_line = pg.InfiniteLine(pos=0,     angle=0,  pen=pg.mkPen('b', width=1), movable=False)
        self._calib_pts     = pg.ScatterPlotItem()
        self._beam_x_curve  = pg.PlotCurveItem(pen=pg.mkPen('g', width=2))
        self._beam_y_curve  = pg.PlotCurveItem(pen=pg.mkPen('g', width=2))

        for item in (self._center_x_line, self._center_y_line,
                     self._cursor_x_line, self._cursor_y_line,
                     self._calib_pts, self._beam_x_curve, self._beam_y_curve):
            self.view_box.addItem(item)

        self._cursor_x_line.hide()
        self._cursor_y_line.hide()
        self._calib_pts.hide()
        self._beam_x_curve.hide()
        self._beam_y_curve.hide()

        # ROI rectangle — yellow, draggable and resizable, synced to EPICS PVs
        self._roi_rect = pg.RectROI(
            [0, 0], [50, 50],
            pen=pg.mkPen('y', width=2),
            handlePen=pg.mkPen('y', width=2),
            handleHoverPen=pg.mkPen('w', width=2),
            movable=True,
        )
        self._roi_rect.addTranslateHandle([0.5, 0.5])   # center handle for translation
        self._roi_rect.sigRegionChangeFinished.connect(self._on_roi_dragged)
        self.view_box.addItem(self._roi_rect)


    # ── Camera controls ────────────────────────────────────────────────────

    def _start_camera(self):
        if self._acquire_pv:
            self._acquire_pv.put(1)

    def _stop_camera(self):
        if self._acquire_pv:
            self._acquire_pv.put(0)

    # ── Acquisition settings ───────────────────────────────────────────────

    def _send_acqtime(self):
        pv = getattr(self, '_acqtime_pv', None)
        if pv is None:
            return
        try:
            pv.put(float(self.acqtime_edit.text()))
        except ValueError:
            pass

    def _send_acqperiod(self):
        pv = getattr(self, '_acqperiod_pv', None)
        if pv is None:
            return
        try:
            pv.put(float(self.acqperiod_edit.text()))
        except ValueError:
            pass

    def _send_imgmode(self, index: int):
        pv = getattr(self, '_imgmode_pv', None)
        if pv is not None:
            pv.put(index)

    @pyqtSlot(str, object)
    def _on_acqtime(self, _pvname, value):
        if not self.acqtime_edit.hasFocus():
            self.acqtime_edit.setText(f"{float(value):.4g}")

    @pyqtSlot(str, object)
    def _on_acqperiod(self, _pvname, value):
        if not self.acqperiod_edit.hasFocus():
            self.acqperiod_edit.setText(f"{float(value):.4g}")

    @pyqtSlot(str, object)
    def _on_imgmode(self, _pvname, value):
        idx = int(value)
        if 0 <= idx < self.imgmode_combo.count():
            self.imgmode_combo.blockSignals(True)
            self.imgmode_combo.setCurrentIndex(idx)
            self.imgmode_combo.blockSignals(False)

    # ── Mouse events ───────────────────────────────────────────────────────

    @pyqtSlot(object)
    def _update_cursor_label(self, pos):
        try:
            coords = self.image_item.mapFromScene(pos)
            x, y   = int(coords.x()), int(coords.y())
        except Exception:
            return
        if self.image is not None and 0 <= x < self.image_width and 0 <= y < self.image_height:
            self.cursor_x, self.cursor_y = x, y
            i = int(self.image[y, x])
            self.cursor_lbl.setText(f"X={x:4d}, Y={y:4d}, I={i:3d}")
            self._cursor_x_line.setValue(x)
            self._cursor_y_line.setValue(y)
            self._cursor_x_line.show()
            self._cursor_y_line.show()
        else:
            self._cursor_x_line.hide()
            self._cursor_y_line.hide()

    def _update_beam_label(self):
        self.beam_lbl.setText(f"BeamX={self.beam_x:4d}, BeamY={self.beam_y:4d}")

    @pyqtSlot(object)
    def _on_camera_click(self, event):
        try:
            coords = self.image_item.mapFromScene(event.scenePos())
            x, y = int(coords.x()), int(coords.y())
        except Exception:
            return
        if self.image is None:
            return
        self.cursor_x, self.cursor_y = x, y

        if 0 <= x < self.image_width and 0 <= y < self.image_height:
            v = self._roi_vals
            ref_x = v['MinX'] + v['SizeX'] / 2
            ref_y = v['MinY'] + v['SizeY'] / 2
            motor_x = self.x_motor.get_sp() + self.cf * (x - ref_x)
            motor_y = self.y_motor.get_sp() + self.cf * (y - ref_y)

            if not event._double and self.auto_add_cb.isChecked():
                # Single click in auto-add mode: record position without moving motors
                if hasattr(self, 'pos_tab'):
                    from position_models import PositionRecord
                    from position_io import normalize_positions
                    z = self.z_motor.get_sp() if hasattr(self.z_motor, 'get_sp') else 0.0
                    pos = PositionRecord(x=round(motor_x, 4), y=round(motor_y, 4),
                                        z=round(z, 4), role="Sample",
                                        layout="freeform").to_dict()
                    self.pos_tab._push_undo()
                    self.pos_tab._positions.append(pos)
                    self.pos_tab._positions = normalize_positions(self.pos_tab._positions)
                    self.pos_tab.set_positions(self.pos_tab._positions)

            elif event._double and self.click_move_cb.isChecked():
                self.x_motor.move_to(motor_x)
                self.y_motor.move_to(motor_y)
                if self.auto_add_cb.isChecked():
                    # Wait for both motors then auto-add — non-blocking via QTimer
                    def _check_and_add(xm=self.x_motor, ym=self.y_motor,
                                       t=QTimer(self), add=self.addPosition):
                        if not xm.is_moving() and not ym.is_moving():
                            t.stop(); t.deleteLater(); add()
                    _t = QTimer(self); _t.setInterval(50)
                    _t.timeout.connect(lambda: _check_and_add(t=_t))
                    _t.start()

        elif self.calibration_flag:
            if self.calib_chosen == 1:
                self.pos1 = [x, y]
                self.calib_pos[0] = [x, y]
                if hasattr(self, '_calib_dialog'):
                    self._calib_dialog.first_lbl.setText(f"x: {x}, y: {y}")
            else:
                self.pos2 = [x, y]
                self.calib_pos[1] = [x, y]
                if hasattr(self, '_calib_dialog'):
                    self._calib_dialog.second_lbl.setText(f"x: {x}, y: {y}")
            self._calib_pts.setData(
                pos=self.calib_pos, size=10, symbol='o', pen=pg.mkPen('red')
            )

        if self.select_beam_cb.isChecked():
            self.beam_x, self.beam_y = x, y
            self._update_beam_label()
            self._beam_x_curve.setData(x=[x, x],         y=[y - 10, y + 10])
            self._beam_y_curve.setData(x=[x - 10, x + 10], y=[y, y])
            self._beam_x_curve.show()
            self._beam_y_curve.show()

    # ── Calibration factor ─────────────────────────────────────────────────

    def _load_calib_file(self):
        try:
            with open(CALIB_FILE) as f:
                for line in f:
                    line = line.strip()
                    if line.startswith('#') or not line:
                        continue
                    if line.startswith('cf='):
                        self.cf = float(line.split('=')[1])
        except Exception:
            pass
        self.cf_edit.setText(f"{self.cf:.6f}")

    def cfChanged(self):
        try:
            self.cf = float(self.cf_edit.text())
        except ValueError:
            QMessageBox.warning(self, "Value Error", "Enter a number only.")
            self.cf_edit.setText(f"{self.cf:.6f}")
            return
        self._save_calib_file()

    def _save_calib_file(self):
        try:
            os.makedirs(os.path.dirname(CALIB_FILE), exist_ok=True)
            with open(CALIB_FILE, 'w') as f:
                f.write(f"#Calibration saved {time.asctime()}\n")
                f.write(f"cf={self.cf:.6f}\n")
        except Exception as e:
            QMessageBox.warning(self, "File Error", f"Could not save calibration:\n{e}")

    # ── Calibration widget ─────────────────────────────────────────────────

    def openCalibration(self):
        self.calib_pos = [
            [self.image_cx, self.image_cy],
            [self.image_cx + 50, self.image_cy + 50],
        ]
        self.calibration_flag = True
        self.calib_chosen = 1
        self._calib_pts.show()

        dlg = _CalibDialog(self)
        self._calib_dialog = dlg
        dlg.first_btn.clicked.connect(lambda: self._select_calib_pos(1))
        dlg.second_btn.clicked.connect(lambda: self._select_calib_pos(2))
        dlg.ok_btn.clicked.connect(lambda: self._do_calibrate(dlg))
        dlg.rejected.connect(self._cancel_calibration)
        dlg.show()

    def _select_calib_pos(self, n: int):
        self.calib_chosen = n

    def _cancel_calibration(self):
        self.calibration_flag = False
        self._calib_pts.hide()

    def _do_calibrate(self, dlg: _CalibDialog):
        try:
            known = float(dlg.dist_edit.text())
        except ValueError:
            QMessageBox.critical(dlg, "Value Error", "Enter a numeric distance.")
            return
        dx = self.pos1[0] - self.pos2[0]
        dy = self.pos1[1] - self.pos2[1]
        pixel_dist = np.sqrt(dx ** 2 + dy ** 2)
        if pixel_dist < 1.0:
            QMessageBox.warning(dlg, "Error", "Points are too close together.")
            return
        self.cf = round(known / pixel_dist, 6)
        self.cf_edit.setText(f"{self.cf:.6f}")
        self.cfChanged()
        self.calibration_flag = False
        self._calib_pts.hide()
        dlg.accept()

    # ── ROI / beam centering ───────────────────────────────────────────────

    @pyqtSlot()
    def _on_roi_spin_changed(self):
        x = self.roi_x_spin.value()
        y = self.roi_y_spin.value()
        s = self.roi_size_spin.value()
        self._roi_vals.update({'MinX': x, 'MinY': y, 'SizeX': s, 'SizeY': s})
        self._update_roi_rect()
        if not EPICS_AVAILABLE:
            return
        roi_prefix = self.cfg.get("ROI_PREFIX", "").strip()
        if not roi_prefix:
            return
        epics.caput(roi_prefix + 'MinX',  x)
        epics.caput(roi_prefix + 'MinY',  y)
        epics.caput(roi_prefix + 'SizeX', s)
        epics.caput(roi_prefix + 'SizeY', s)

    @pyqtSlot()
    def _on_roi_center(self):
        sx = self._roi_vals['SizeX']
        sy = self._roi_vals['SizeY']
        new_x = max(0, self.image_cx - sx // 2)
        new_y = max(0, self.image_cy - sy // 2)
        self._roi_vals.update({'MinX': new_x, 'MinY': new_y, 'SizeX': sx, 'SizeY': sy})
        self._update_roi_rect()
        if EPICS_AVAILABLE:
            roi_prefix = self.cfg.get("ROI_PREFIX", "").strip()
            if roi_prefix:
                epics.caput(roi_prefix + 'MinX', new_x)
                epics.caput(roi_prefix + 'MinY', new_y)

    def _wait_motor_done(self, motor, callback=None):
        """Poll motor MOVN every 50 ms via QTimer — no processEvents blocking."""
        timer = QTimer(self)
        timer.setInterval(50)
        def _check():
            if not motor.is_moving():
                timer.stop()
                timer.deleteLater()
                if callback:
                    callback()
        timer.timeout.connect(_check)
        timer.start()

    # ── Autofocus ──────────────────────────────────────────────────────────

    def _run_autofocus(self):
        if self._af_thread is not None and self._af_thread.isRunning():
            return  # already running — button should be hidden, safety guard

        cam  = self.cfg.get("CAMERA_PREFIX", "").strip()
        img  = self.cfg.get("IMAGE_PREFIX",  "").strip()
        zpv  = self.cfg.get("Z_MOTOR_PV",    "").strip()
        try:
            step = float(self.cfg.get("AUTOFOCUS_STEP", "0.2"))
        except ValueError:
            step = 0.2

        if not cam or not img or not zpv:
            QMessageBox.warning(self, "Autofocus",
                "Camera Prefix, Image Prefix, and Z Motor PV must be set in Setup.")
            return

        self._af_worker = _AutofocusWorker()
        self._af_thread = QThread(self)
        self._af_worker.moveToThread(self._af_thread)

        self._af_worker.progress.connect(
            lambda msg: self.focus_lbl.setToolTip(msg))
        self._af_worker.focus_val.connect(
            lambda fp: self.focus_lbl.setText(f"{fp:.1f}*"))
        self._af_worker.finished.connect(self._on_af_finished)

        self._af_thread.started.connect(
            lambda: self._af_worker.run(cam, img, zpv, step))
        self._af_thread.start()

        self.autofocus_btn.setEnabled(False)
        self.autofocus_btn.setText("Running…")
        self.af_cancel_btn.setVisible(True)
        self.focus_lbl.setToolTip("")

    def _cancel_autofocus(self):
        if self._af_worker is not None:
            self._af_worker.cancel()

    @pyqtSlot(bool, str)
    def _on_af_finished(self, ok: bool, msg: str):
        if self._af_thread is not None:
            self._af_thread.quit()
            self._af_thread.wait(3000)
        self._af_thread = None
        self._af_worker = None

        self.autofocus_btn.setEnabled(True)
        self.autofocus_btn.setText("Autofocus")
        self.af_cancel_btn.setVisible(False)

        if ok:
            self.focus_lbl.setToolTip(f"Last autofocus: {msg}")
            # focus_val signal already updated the label value
        else:
            self.focus_lbl.setToolTip(f"Autofocus stopped: {msg}")
            if not msg.startswith("Cancelled"):
                QMessageBox.warning(self, "Autofocus Error", msg)

    # ── Position capture ───────────────────────────────────────────────────

    def addPosition(self):
        if hasattr(self, 'pos_tab'):
            self.pos_tab._capture_from_stage()

    def closeEvent(self, event):
        self._stop_pva_thread()
        if self._af_thread is not None and self._af_thread.isRunning():
            if self._af_worker is not None:
                self._af_worker.cancel()
            self._af_thread.quit()
            self._af_thread.wait(3000)
        super().closeEvent(event)

    # ── Style ──────────────────────────────────────────────────────────────

    def _apply_style(self):
        apply_style(self)


# ── Entry point ────────────────────────────────────────────────────────────────

def main():
    app = QApplication(sys.argv)
    w = SampleStation()
    w.resize(1500, 960)
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
