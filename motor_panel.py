# motor_panel.py — Single-motor EPICS control panel with sub-widgets for a shared QGridLayout.
# Debug entry point: check _on_conn firing for .RBV and _on_pv for each field suffix.
# External deps: {X,Y,Z}_MOTOR_PV.DESC/.RBV/.VAL/.MOVN/.TWV/.EGU/.TWR/.TWF/.STOP

from PyQt6.QtWidgets import QLabel, QLineEdit, QPushButton
from PyQt6.QtCore import Qt, pyqtSlot, QObject

from epics_bridge import _PVBridge

try:
    import epics
    from epics import Motor, PV
    import epics.ca as _ca
    _ca.initialize_libca()
    EPICS_AVAILABLE = True
except Exception:
    EPICS_AVAILABLE = False
    epics = None   # type: ignore[assignment]
    Motor = None
    PV = None


class MotorPanel(QObject):
    """Motor EPICS logic — exposes sub-widgets for placement in a shared QGridLayout."""

    def __init__(self, label="Motor", parent=None):
        super().__init__(parent)
        self._label = label
        self._base = ""
        self._pvs: dict[str, PV] = {}
        self._motor = None
        self._bridge = _PVBridge()
        self._bridge.changed.connect(self._on_pv)
        self._bridge.conn_state.connect(self._on_conn)
        self._build_widgets()

    def _build_widgets(self):
        # Axis badge — doubles as PV connection indicator
        self.axis_lbl = QLabel(self._label[0])
        self.axis_lbl.setFixedWidth(28)
        self.axis_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.axis_lbl.setObjectName("axisLabel")
        self._set_conn("idle")

        # DESC readback
        self.desc_lbl = QLabel("—")
        self.desc_lbl.setFixedWidth(110)
        self.desc_lbl.setObjectName("descLabel")

        # RBV tag + value + EGU
        self.rbv_tag = QLabel("RBV")
        self.rbv_tag.setFixedWidth(28)
        self.rbv_tag.setObjectName("fieldTag")

        self.rbv_lbl = QLabel("—")
        self.rbv_lbl.setFixedWidth(75)
        self.rbv_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.rbv_lbl.setObjectName("rbvLabel")

        self.egu_lbl = QLabel("")
        self.egu_lbl.setFixedWidth(26)
        self.egu_lbl.setObjectName("eguLabel")

        # SP tag + edit
        self.sp_tag = QLabel("SP")
        self.sp_tag.setFixedWidth(22)
        self.sp_tag.setObjectName("fieldTag")

        self.sp_edit = QLineEdit()
        self.sp_edit.setFixedWidth(75)
        self.sp_edit.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.sp_edit.returnPressed.connect(self._send_sp)

        # MOVN indicator
        self.movn_lbl = QLabel("⬤")
        self.movn_lbl.setFixedWidth(18)
        self.movn_lbl.setObjectName("movnIdle")

        # Step tag + edit
        self.step_tag = QLabel("Step")
        self.step_tag.setFixedWidth(36)
        self.step_tag.setObjectName("fieldTag")

        self.step_edit = QLineEdit("0.100")
        self.step_edit.setFixedWidth(58)
        self.step_edit.returnPressed.connect(self._send_twv)

        # Tweak buttons
        self.rev_btn = QPushButton("◀")
        self.rev_btn.setFixedSize(26, 24)
        self.rev_btn.setObjectName("tweakBtn")
        self.rev_btn.clicked.connect(self._tweak_rev)

        self.fwd_btn = QPushButton("▶")
        self.fwd_btn.setFixedSize(26, 24)
        self.fwd_btn.setObjectName("tweakBtn")
        self.fwd_btn.clicked.connect(self._tweak_fwd)

        # Stop button — same visual style as tweak buttons
        self.stop_btn = QPushButton("■")
        self.stop_btn.setFixedSize(26, 24)
        self.stop_btn.setObjectName("tweakBtn")
        self.stop_btn.setToolTip("Stop motor")
        self.stop_btn.clicked.connect(self._stop_motor)

    def place_in_grid(self, grid, row: int, col_offset: int = 0):
        """Place all sub-widgets into an external QGridLayout at the given row."""
        A = Qt.AlignmentFlag
        c = col_offset
        grid.addWidget(self.axis_lbl,  row, c+0,  A.AlignCenter)
        grid.addWidget(self.desc_lbl,  row, c+1)
        grid.addWidget(self.rbv_tag,   row, c+2,  A.AlignRight | A.AlignVCenter)
        grid.addWidget(self.rbv_lbl,   row, c+3)
        grid.addWidget(self.egu_lbl,   row, c+4,  A.AlignLeft | A.AlignVCenter)
        grid.addWidget(self.sp_tag,    row, c+5,  A.AlignRight | A.AlignVCenter)
        grid.addWidget(self.sp_edit,   row, c+6)
        grid.addWidget(self.movn_lbl,  row, c+7,  A.AlignCenter)
        grid.addWidget(self.step_tag,  row, c+8,  A.AlignRight | A.AlignVCenter)
        grid.addWidget(self.step_edit, row, c+9)
        grid.addWidget(self.rev_btn,   row, c+10)
        grid.addWidget(self.fwd_btn,   row, c+11)
        grid.addWidget(self.stop_btn,  row, c+12)

    # ── public API ─────────────────────────────────────────────────────────

    def connect(self, base_pv: str):
        self._disconnect()
        self._base = base_pv
        if not (EPICS_AVAILABLE and base_pv):
            self._set_conn("idle")
            return
        self._set_conn("connecting")
        try:
            monitored   = ("DESC", "RBV", "VAL", "MOVN", "TWV", "EGU")
            unmonitored = ("TWR", "TWF", "STOP")
            for f in monitored:
                kw = dict(callback=self._bridge, auto_monitor=True)
                if f == "RBV":
                    kw["connection_callback"] = self._bridge.conn_cb
                self._pvs[f] = PV(f"{base_pv}.{f}", **kw)
            for f in unmonitored:
                self._pvs[f] = PV(f"{base_pv}.{f}")
            self._motor = Motor(base_pv)
        except Exception as e:
            print(f"Motor({base_pv}) connection failed: {e}")
            self._set_conn("lost")

    def get_rbv(self) -> float:
        try:
            return float(self.rbv_lbl.text())
        except ValueError:
            return 0.0

    def get_sp(self) -> float:
        try:
            return float(self.sp_edit.text())
        except ValueError:
            return 0.0

    def move_to(self, value: float):
        self.sp_edit.setText(f"{value:.3f}")
        self._send_sp()

    def is_moving(self) -> bool:
        m = self._motor
        if m is None:
            return False
        try:
            return bool(m.MOVN)
        except Exception:
            return False

    # ── private ────────────────────────────────────────────────────────────

    def _disconnect(self):
        for pv in self._pvs.values():
            try:
                pv.disconnect()
            except Exception:
                pass
        self._pvs.clear()
        self._motor = None
        self._set_conn("idle")

    @pyqtSlot(str, object)
    def _on_pv(self, pvname: str, value):
        b = self._base
        if pvname == f"{b}.DESC":
            self.desc_lbl.setText(str(value))
        elif pvname == f"{b}.RBV":
            self.rbv_lbl.setText(f"{float(value):.3f}")
        elif pvname == f"{b}.VAL":
            if not self.sp_edit.hasFocus():
                self.sp_edit.setText(f"{float(value):.3f}")
        elif pvname == f"{b}.MOVN":
            moving = bool(int(value))
            self.movn_lbl.setObjectName("movnActive" if moving else "movnIdle")
            self.movn_lbl.style().unpolish(self.movn_lbl)
            self.movn_lbl.style().polish(self.movn_lbl)
        elif pvname == f"{b}.TWV":
            if not self.step_edit.hasFocus():
                self.step_edit.setText(f"{float(value):.3f}")
        elif pvname == f"{b}.EGU":
            egu = str(value).strip()
            self.egu_lbl.setText(egu)
            self.rbv_lbl.setToolTip(f"Units: {egu}")
            self.sp_edit.setToolTip(f"Units: {egu}")
            self.step_edit.setToolTip(f"Step units: {egu}")

    _CONN_STYLE = {
        "idle":       "background:#9ca3af; color:white;",
        "connecting": "background:#d97706; color:white;",
        "ok":         "background:#16a34a; color:white;",
        "lost":       "background:#dc2626; color:white;",
    }
    _CONN_TIP = {
        "idle":       "No PV configured",
        "connecting": "Connecting…",
        "ok":         "Connected",
        "lost":       "Connection lost",
    }

    def _set_conn(self, state: str):
        style = self._CONN_STYLE.get(state, self._CONN_STYLE["idle"])
        base_style = (
            f"{style} border-radius:4px; font-weight:bold;"
            " font-size:9pt; padding:1px 2px;"
        )
        self.axis_lbl.setStyleSheet(base_style)
        self.axis_lbl.setToolTip(
            f"{self._label} — {self._CONN_TIP.get(state, '')}"
            + (f"\nPV: {self._base}" if self._base else "")
        )

    @pyqtSlot(str, bool)
    def _on_conn(self, pvname: str, conn: bool):
        if pvname == f"{self._base}.RBV":
            self._set_conn("ok" if conn else "lost")

    def _send_sp(self):
        pv = self._pvs.get("VAL")
        if pv is None:
            return
        try:
            pv.put(float(self.sp_edit.text()))
        except ValueError:
            pass

    def _send_twv(self):
        pv = self._pvs.get("TWV")
        if pv is None:
            return
        try:
            pv.put(float(self.step_edit.text()))
        except ValueError:
            pass

    def _tweak_rev(self):
        self._send_twv()
        pv = self._pvs.get("TWR")
        if pv:
            pv.put(1)

    def _tweak_fwd(self):
        self._send_twv()
        pv = self._pvs.get("TWF")
        if pv:
            pv.put(1)

    def _stop_motor(self):
        pv = self._pvs.get("STOP")
        if pv:
            pv.put(1)
