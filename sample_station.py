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
        self.roisize       = 60
        self.x_offset      = 0.0
        self.y_offset      = 0.0
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
        fm.addAction("New Positions",        lambda: self.pos_tab._new())
        fm.addAction("Open Positions…",      lambda: self.pos_tab._open())
        fm.addAction("Save Positions",       lambda: self.pos_tab._save())
        fm.addAction("Save Positions As…",   lambda: self.pos_tab._save_as())
        fm.addSeparator()
        ex = fm.addMenu("Export")
        ex.addAction("Bluesky CSV…",         lambda: self.pos_tab._export_bluesky())
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

        self.x_motor = MotorPanel("X")
        self.y_motor = MotorPanel("Y")
        self.z_motor = MotorPanel("Z (Focus)")

        # Single shared grid — all motor sub-widgets share column definitions
        grid_w = QWidget()
        grid = QGridLayout(grid_w)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(4)
        grid.setVerticalSpacing(0)
        grid.setColumnStretch(13, 1)   # spacer between motor cols and right extras

        # Motor rows at grid rows 0, 2, 4; separators at 1, 3
        self.x_motor.place_in_grid(grid, 0)
        self.y_motor.place_in_grid(grid, 2)
        self.z_motor.place_in_grid(grid, 4)

        sep0 = QFrame(); sep0.setFrameShape(QFrame.Shape.HLine); sep0.setObjectName("motorSep")
        sep1 = QFrame(); sep1.setFrameShape(QFrame.Shape.HLine); sep1.setObjectName("motorSep")
        grid.addWidget(sep0, 1, 0, 1, 15)
        grid.addWidget(sep1, 3, 0, 1, 15)

        # Right-side extras in column 14
        # X row: ROI
        roi_w = QWidget()
        roi_h = QHBoxLayout(roi_w)
        roi_h.setContentsMargins(0, 0, 0, 0)
        roi_h.setSpacing(4)
        roi_h.addWidget(QLabel("ROI:"))
        self.roi_edit = QLineEdit("60")
        self.roi_edit.setFixedWidth(44)
        roi_h.addWidget(self.roi_edit)
        grid.addWidget(roi_w, 0, 14)

        # Y row: offset label
        self.offset_label = QLabel("Offset: X=0.000000, Y=0.000000")
        self.offset_label.setObjectName("offsetLabel")
        self.offset_label.setVisible(False)
        grid.addWidget(self.offset_label, 2, 14)

        # Z row: action buttons
        btns_w = QWidget()
        btns_h = QHBoxLayout(btns_w)
        btns_h.setContentsMargins(0, 0, 0, 0)
        btns_h.setSpacing(4)
        self.calc_offset_btn = QPushButton("Calc Offset")
        self.calc_offset_btn.setObjectName("actionBtn")
        self.calc_offset_btn.setFixedHeight(26)
        self.calc_offset_btn.setVisible(False)
        self.center_x_btn = QPushButton("Cen X")
        self.center_x_btn.setObjectName("actionBtn")
        self.center_x_btn.setFixedHeight(26)
        self.center_x_btn.setVisible(False)
        self.center_y_btn = QPushButton("Cen Y")
        self.center_y_btn.setObjectName("actionBtn")
        self.center_y_btn.setFixedHeight(26)
        self.center_y_btn.setVisible(False)
        btns_h.addWidget(self.calc_offset_btn)
        btns_h.addWidget(self.center_x_btn)
        btns_h.addWidget(self.center_y_btn)
        grid.addWidget(btns_w, 4, 14)

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

        toolbar.addWidget(QLabel("Focus:"))
        self.focus_lbl = QLabel("—")
        self.focus_lbl.setFixedWidth(75)
        self.focus_lbl.setObjectName("focusLabel")
        toolbar.addWidget(self.focus_lbl)
        self.autofocus_btn = QPushButton("Autofocus")
        toolbar.addWidget(self.autofocus_btn)
        self.af_cancel_btn = QPushButton("Cancel AF")
        self.af_cancel_btn.setObjectName("cancelBtn")
        self.af_cancel_btn.setVisible(False)
        toolbar.addWidget(self.af_cancel_btn)

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.VLine)
        sep2.setFrameShadow(QFrame.Shadow.Sunken)
        toolbar.addWidget(sep2)

        self.cam_start_btn = QPushButton("▶ Start")
        toolbar.addWidget(self.cam_start_btn)
        self.cam_stop_btn = QPushButton("■ Stop")
        toolbar.addWidget(self.cam_stop_btn)
        self.cam_state_lbl = QLabel("Camera: —")
        self.cam_state_lbl.setObjectName("camStateLabel")
        toolbar.addWidget(self.cam_state_lbl)

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
        self.roi_lock_btn = QPushButton("Lock ROI")
        self.roi_lock_btn.setCheckable(True)
        self.roi_lock_btn.setFixedWidth(85)
        roi_bar.addWidget(self.roi_lock_btn)
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
        self.calc_offset_btn.clicked.connect(self._calc_offset)
        self.center_x_btn.clicked.connect(self._center_x)
        self.center_y_btn.clicked.connect(self._center_y)
        self.roi_edit.returnPressed.connect(self.roiSizeChanged)

        # Camera tab
        self.cf_edit.returnPressed.connect(self.cfChanged)
        self.add_pos_btn.clicked.connect(self.addPosition)
        self.calibrate_btn.clicked.connect(self.openCalibration)
        self.autofocus_btn.clicked.connect(self._run_autofocus)
        self.af_cancel_btn.clicked.connect(self._cancel_autofocus)
        self.cam_start_btn.clicked.connect(self._start_camera)
        self.cam_stop_btn.clicked.connect(self._stop_camera)

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
        self.pos_tab.set_axis_names(
            self.cfg.get("X_MOTOR_NAME", "s_x"),
            self.cfg.get("Y_MOTOR_NAME", "s_y"),
            self.cfg.get("Z_MOTOR_NAME", "s_z"),
        )

    def _apply_camera_config(self):
        # Stop any pending connection timeout
        if hasattr(self, '_cam_conn_timer'):
            self._cam_conn_timer.stop()

        for attr in ('_img_pv', '_wid_pv', '_hgt_pv', '_state_pv'):
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

        cam = self.cfg.get("CAMERA_PREFIX", "").strip()
        img = self.cfg.get("IMAGE_PREFIX",  "").strip()
        if not cam or not img:
            self.cam_state_lbl.setText("● Camera: prefix not configured")
            self.cam_state_lbl.setStyleSheet("color: #dc2626; font-size: 8.5pt;")
            return

        try:
            self._img_bridge   = _PVBridge()
            self._wid_bridge   = _PVBridge()
            self._hgt_bridge   = _PVBridge()
            self._state_bridge = _PVBridge()
            self._acq_bridge   = _PVBridge()

            self._img_bridge.changed.connect(self._on_image_data)
            self._wid_bridge.changed.connect(self._on_width_data)
            self._hgt_bridge.changed.connect(self._on_height_data)
            self._state_bridge.changed.connect(self._on_cam_state)
            self._state_bridge.conn_state.connect(self._on_cam_conn)
            self._acq_bridge.conn_state.connect(self._on_acquire_conn)

            self._img_pv   = PV(img + "ArrayData",
                                callback=self._img_bridge, auto_monitor=True)
            self._wid_pv   = PV(cam + "ArraySizeX_RBV",
                                callback=self._wid_bridge, auto_monitor=True)
            self._hgt_pv   = PV(cam + "ArraySizeY_RBV",
                                callback=self._hgt_bridge, auto_monitor=True)
            self._state_pv = PV(cam + "DetectorState_RBV",
                                callback=self._state_bridge,
                                connection_callback=self._state_bridge.conn_cb,
                                auto_monitor=True)

            # Acquire PV — call put(1) immediately (pyepics queues it until connected)
            # Also attach connection_callback as a fallback for reconfigure calls
            if self._acquire_pv is not None:
                try:
                    self._acquire_pv.disconnect()
                except Exception:
                    pass
            self._acquire_pv = PV(cam + "Acquire",
                                  connection_callback=self._acq_bridge.conn_cb)
            atexit.register(self._stop_acquire)
            self._acquire_pv.put(1)   # pyepics queues internally if not yet connected

            # Show connecting state immediately
            self.cam_state_lbl.setText("● Camera: connecting…")
            self.cam_state_lbl.setStyleSheet("color: #d97706; font-size: 8.5pt;")

            # Timeout: if DetectorState_RBV has not connected in 5 s, warn user
            self._cam_conn_timer = QTimer(self)
            self._cam_conn_timer.setSingleShot(True)
            self._cam_conn_timer.timeout.connect(self._on_cam_conn_timeout)
            self._cam_conn_timer.start(5000)

        except Exception as e:
            print(f"Camera PV setup error: {e}")
            self.cam_state_lbl.setText(f"● Camera: error — {e}")
            self.cam_state_lbl.setStyleSheet("color: #dc2626; font-size: 8.5pt;")

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
        s = v['SizeX']   # canonical square size
        self._roi_rect.blockSignals(True)
        self._roi_rect.setPos([v['MinX'], v['MinY']])
        self._roi_rect.setSize([s, s])
        self._roi_rect.blockSignals(False)
        for spin, val in ((self.roi_x_spin, v['MinX']),
                          (self.roi_y_spin, v['MinY']),
                          (self.roi_size_spin, s)):
            spin.blockSignals(True)
            spin.setValue(val)
            spin.blockSignals(False)

    @pyqtSlot()
    def _on_roi_spin_changed(self):
        """Slot: user edited a ROI spinbox — write to EPICS."""
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

    @pyqtSlot(bool)
    def _on_roi_lock_toggled(self, locked: bool):
        """Slot: Lock ROI button toggled."""
        self.roi_lock_btn.setText("Locked" if locked else "Lock ROI")
        pen = pg.mkPen('gray', width=2) if locked else pg.mkPen('y', width=2)
        self._roi_rect.setPen(pen)
        self.roi_x_spin.setEnabled(not locked)
        self.roi_y_spin.setEnabled(not locked)
        self.roi_size_spin.setEnabled(not locked)

    @pyqtSlot()
    def _on_roi_center(self):
        """Slot: Center ROI button — move ROI so its center coincides with image center."""
        if self.roi_lock_btn.isChecked():
            return
        s = self._roi_vals['SizeX']  # current square size
        # compute MinX/MinY so ROI center = image center
        new_x = max(0, self.image_cx - s // 2)
        new_y = max(0, self.image_cy - s // 2)
        self._roi_vals.update({'MinX': new_x, 'MinY': new_y, 'SizeX': s, 'SizeY': s})
        self._update_roi_rect()
        if EPICS_AVAILABLE:
            roi_prefix = self.cfg.get("ROI_PREFIX", "").strip()
            if roi_prefix:
                epics.caput(roi_prefix + 'MinX',  new_x)
                epics.caput(roi_prefix + 'MinY',  new_y)

    @pyqtSlot(object)
    def _on_roi_dragged(self, _roi):
        """Slot: user finished dragging/resizing the ROI — write back to EPICS."""
        if self.roi_lock_btn.isChecked():
            self._update_roi_rect()   # snap back to locked position
            return
        if not EPICS_AVAILABLE:
            return
        roi_prefix = self.cfg.get("ROI_PREFIX", "").strip()
        if not roi_prefix:
            return
        pos  = self._roi_rect.pos()
        size = self._roi_rect.size()
        x, y = max(0, int(pos.x())), max(0, int(pos.y()))
        s = max(1, int(size.x()), int(size.y()))   # square: take larger dimension
        # Store first so echo-back in _on_roi_pv is recognised and suppressed
        self._roi_vals.update({'MinX': x, 'MinY': y, 'SizeX': s, 'SizeY': s})
        self._update_roi_rect()   # snap to square immediately
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

        self.roi_x_spin.editingFinished.connect(self._on_roi_spin_changed)
        self.roi_y_spin.editingFinished.connect(self._on_roi_spin_changed)
        self.roi_size_spin.editingFinished.connect(self._on_roi_spin_changed)
        self.roi_lock_btn.toggled.connect(self._on_roi_lock_toggled)
        self.roi_center_btn.clicked.connect(self._on_roi_center)

    # ── Camera controls ────────────────────────────────────────────────────

    def _start_camera(self):
        if self._acquire_pv:
            self._acquire_pv.put(1)

    def _stop_camera(self):
        if self._acquire_pv:
            self._acquire_pv.put(0)

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
        x, y = self.cursor_x, self.cursor_y
        if self.image is None:
            return

        if event._double and self.click_move_cb.isChecked():
            if 0 <= x < self.image_width and 0 <= y < self.image_height:
                # ROI center = beam position on the detector (top-left corner + half size)
                v = self._roi_vals
                ref_x = v['MinX'] + v['SizeX'] / 2  # beam pixel X
                ref_y = v['MinY'] + v['SizeY'] / 2  # beam pixel Y
                new_x = self.x_motor.get_sp() + self.cf * (x - ref_x)
                new_y = self.y_motor.get_sp() + self.cf * (y - ref_y)
                self.x_motor.move_to(new_x)
                self.y_motor.move_to(new_y)
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

    def roiSizeChanged(self):
        try:
            self.roisize = int(self.roi_edit.text())
        except ValueError:
            QMessageBox.warning(self, "Value Error", "Integer only.")
            self.roi_edit.setText(str(self.roisize))

    def _calc_offset(self):
        if self.image is None:
            QMessageBox.warning(self, "No Image", "Camera not streaming.")
            return
        cx, cy, r = self.image_cx, self.image_cy, self.roisize
        roi1 = self.image[cy - r:cy,       cx - r:cx    ]
        roi2 = self.image[cy - r:cy,       cx:cx + r    ]
        roi3 = self.image[cy:cy + r,       cx:cx + r    ]
        roi4 = self.image[cy:cy + r,       cx - r:cx    ]

        int_max  = max(r_.max() for r_ in (roi1, roi2, roi3, roi4))
        rois     = [np.abs(r_ - int_max) for r_ in (roi1, roi2, roi3, roi4)]
        int_max2 = max(r_.max() for r_ in rois)
        thresh   = 0.1 * int_max2

        s     = [np.sum(r_ > thresh) for r_ in rois]
        right = int(s[1] + s[2])
        left  = int(s[0] + s[3])
        top   = int(s[0] + s[1])
        bot   = int(s[2] + s[3])
        total = left + right
        if total == 0:
            return
        # Convert pixel imbalance to motor units (mm) using the calibration factor.
        # ROI spans 2*r pixels = 2*r*cf mm. The imbalance ratio (right-left)/total
        # is in [-1, 1], so the offset in mm is that ratio × half the ROI width.
        roi_half_mm = self.roisize * self.cf
        self.x_offset = (right - left) / total * roi_half_mm
        self.y_offset = (top   - bot ) / total * roi_half_mm
        self.offset_label.setText(
            f"Offset: X={self.x_offset:.4f}, Y={self.y_offset:.4f} mm"
        )

    def _center_x(self):
        self._calc_offset()
        if abs(self.x_offset) > 0.005:
            self.x_motor.move_to(self.x_motor.get_sp() - self.x_offset)
            self._wait_motor_done(self.x_motor, self._calc_offset)

    def _center_y(self):
        self._calc_offset()
        if abs(self.y_offset) > 0.005:
            self.y_motor.move_to(self.y_motor.get_sp() - self.y_offset)
            self._wait_motor_done(self.y_motor, self._calc_offset)

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
