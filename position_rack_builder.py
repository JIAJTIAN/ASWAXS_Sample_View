# position_rack_builder.py — RackCanvas paint widget and RackBuilderDialog for capillary rack layout.
# Debug entry point: check _refresh_table() combo wiring and _apply_coordinates() index math.
# External deps: none.

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QDialog, QDialogButtonBox, QSplitter,
    QTableWidget, QTableWidgetItem, QAbstractItemView, QFormLayout, QLabel,
    QLineEdit, QPushButton, QDoubleSpinBox, QSpinBox, QComboBox,
)
from PyQt6.QtCore import Qt, pyqtSignal, QItemSelectionModel, QRectF
from PyQt6.QtGui import QFont, QPainter, QColor, QPen, QBrush

from position_models import POSITION_FIELDS, NUMERIC_FIELDS, ROLE_PRESETS, PositionRecord
from position_io import normalize_positions, _flt


# ── Rack canvas colours ────────────────────────────────────────────────────────

_RACK_ROLES  = ["Sample", "Solvent", "GC", "Air", "Empty", "Skip"]
_RACK_COLORS = {
    "Sample":  QColor("#5b8ff9"),
    "Solvent": QColor("#5ad8a6"),
    "GC":      QColor("#f6bd16"),
    "Air":     QColor("#e86452"),
    "Empty":   QColor("#6dc8ec"),
    "Skip":    QColor("#d8dce3"),
}


class RackCanvas(QWidget):
    selectedChanged = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(230)
        self.setMouseTracking(True)
        self.positions     = []
        self.selected_index = 0

    def set_positions(self, positions):
        self.positions      = positions
        self.selected_index = min(self.selected_index, max(0, len(positions) - 1))
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), self.palette().base())
        if not self.positions:
            return
        margin_x    = 32
        rack_top    = 92
        rack_height = 96
        painter.setPen(QPen(QColor("#111111"), 4))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(margin_x, rack_top, self.width() - 2 * margin_x, rack_height)
        slot_w = 18
        slot_h = 116
        y      = rack_top - 42
        for i, pos in enumerate(self.positions):
            cx   = self._cx(i)
            rect = QRectF(cx - slot_w / 2, y, slot_w, slot_h)
            role  = str(pos.get("role", "Skip") or "Skip")
            color = _RACK_COLORS.get(role, _RACK_COLORS["Skip"])
            painter.setBrush(QBrush(color.lighter(145)))
            pen = QPen(QColor("#111111"), 3)
            if i == self.selected_index:
                pen = QPen(QColor("#000000"), 5)
            painter.setPen(pen)
            painter.drawRoundedRect(rect, 6, 6)
            painter.setPen(QColor("#222222"))
            painter.setFont(QFont("Arial", 8))
            painter.drawText(
                QRectF(cx - 26, y + slot_h + 5, 52, 18),
                Qt.AlignmentFlag.AlignCenter,
                str(i + 1),
            )
            label = str(pos.get("name", "")).strip() or role
            painter.drawText(
                QRectF(cx - 42, y - 26, 84, 22),
                Qt.AlignmentFlag.AlignCenter,
                label[:12],
            )

    def mousePressEvent(self, event):
        if not self.positions:
            return
        dist, idx = min(
            (abs(event.position().x() - self._cx(i)), i)
            for i in range(len(self.positions))
        )
        if dist <= 28:
            self.selected_index = idx
            self.selectedChanged.emit(idx)
            self.update()

    def _cx(self, index):
        n     = max(1, len(self.positions))
        left  = 62
        right = max(63, self.width() - 62)
        return (left + right) / 2 if n == 1 else left + (right - left) * index / (n - 1)


# ── Rack builder dialog ────────────────────────────────────────────────────────

class RackBuilderDialog(QDialog):
    def __init__(self, positions=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Rack Builder")
        self.resize(960, 620)
        self._positions  = normalize_positions(list(positions)) if positions else []
        self._updating   = False
        self._build_ui()
        if not self._positions:
            self._seed_positions(13)
        else:
            self._refresh_table()

    # ── UI construction ────────────────────────────────────────────────────

    def _build_ui(self):
        main = QVBoxLayout(self)
        main.setContentsMargins(8, 8, 8, 8)
        main.setSpacing(6)

        # Top controls
        ctrl = QHBoxLayout()
        ctrl.addWidget(QLabel("Slots:"))
        self._count_spin = QSpinBox()
        self._count_spin.setRange(1, 200)
        self._count_spin.setValue(13)
        self._count_spin.valueChanged.connect(self.set_group_count)
        ctrl.addWidget(self._count_spin)

        ctrl.addSpacing(12)
        ctrl.addWidget(QLabel("Start X (mm):"))
        self._start_x = QDoubleSpinBox()
        self._start_x.setRange(-999, 999)
        self._start_x.setDecimals(3)
        self._start_x.setValue(0.0)
        ctrl.addWidget(self._start_x)

        ctrl.addWidget(QLabel("Spacing (mm):"))
        self._spacing = QDoubleSpinBox()
        self._spacing.setRange(-999, 999)
        self._spacing.setDecimals(3)
        self._spacing.setValue(3.0)
        ctrl.addWidget(self._spacing)

        ctrl.addWidget(QLabel("Y (mm):"))
        self._fixed_y = QDoubleSpinBox()
        self._fixed_y.setRange(-999, 999)
        self._fixed_y.setDecimals(3)
        self._fixed_y.setValue(0.0)
        ctrl.addWidget(self._fixed_y)

        ctrl.addWidget(QLabel("Z (mm):"))
        self._fixed_z = QDoubleSpinBox()
        self._fixed_z.setRange(-999, 999)
        self._fixed_z.setDecimals(3)
        self._fixed_z.setValue(0.0)
        ctrl.addWidget(self._fixed_z)

        apply_coord_btn = QPushButton("Apply Coordinates")
        apply_coord_btn.setObjectName("actionBtn")
        apply_coord_btn.clicked.connect(self._apply_coordinates)
        ctrl.addWidget(apply_coord_btn)
        ctrl.addStretch()
        main.addLayout(ctrl)

        # Role quick-assign buttons
        role_row = QHBoxLayout()
        role_row.addWidget(QLabel("Set selected →"))
        for role in _RACK_ROLES:
            btn = QPushButton(role)
            btn.setFixedHeight(24)
            qc = _RACK_COLORS.get(role, QColor("#aaa"))
            btn.setStyleSheet(
                f"QPushButton{{background:{qc.name()};border:1px solid #888;"
                f"border-radius:3px;padding:1px 6px;}}"
                f"QPushButton:hover{{background:{qc.lighter(115).name()};}}"
            )
            btn.clicked.connect(lambda checked, r=role: self._set_selected_role(r))
            role_row.addWidget(btn)
        role_row.addStretch()
        main.addLayout(role_row)

        # Splitter: table | editor | canvas
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Table
        self._table = QTableWidget(0, len(POSITION_FIELDS))
        self._table.setHorizontalHeaderLabels(POSITION_FIELDS)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._table.setAlternatingRowColors(True)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.itemChanged.connect(self._table_item_changed)
        self._table.selectionModel().selectionChanged.connect(self._table_selection_changed)
        splitter.addWidget(self._table)

        # Editor panel
        editor_w = QWidget()
        editor_w.setFixedWidth(220)
        ef = QFormLayout(editor_w)
        ef.setSpacing(6)
        self._ed_name  = QLineEdit()
        self._ed_x     = QDoubleSpinBox(); self._ed_x.setRange(-9999, 9999); self._ed_x.setDecimals(4)
        self._ed_y     = QDoubleSpinBox(); self._ed_y.setRange(-9999, 9999); self._ed_y.setDecimals(4)
        self._ed_z     = QDoubleSpinBox(); self._ed_z.setRange(-9999, 9999); self._ed_z.setDecimals(4)
        self._ed_role  = QComboBox(); self._ed_role.addItems(ROLE_PRESETS)
        self._ed_group = QLineEdit()
        self._ed_solvent_group = QLineEdit()
        self._ed_note  = QLineEdit()
        ef.addRow("Name:",          self._ed_name)
        ef.addRow("X (mm):",        self._ed_x)
        ef.addRow("Y (mm):",        self._ed_y)
        ef.addRow("Z (mm):",        self._ed_z)
        ef.addRow("Role:",          self._ed_role)
        ef.addRow("Group:",         self._ed_group)
        ef.addRow("Solvent group:", self._ed_solvent_group)
        ef.addRow("Note:",          self._ed_note)
        apply_btn = QPushButton("Apply to selected")
        apply_btn.clicked.connect(self._apply_editor)
        ef.addRow(apply_btn)
        splitter.addWidget(editor_w)

        # Rack canvas
        self._canvas = RackCanvas()
        self._canvas.selectedChanged.connect(self._select_row)
        splitter.addWidget(self._canvas)

        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 0)
        splitter.setStretchFactor(2, 2)
        main.addWidget(splitter, 1)

        # Dialog buttons
        bbox = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        bbox.accepted.connect(self.accept)
        bbox.rejected.connect(self.reject)
        main.addWidget(bbox)

    # ── Data management ────────────────────────────────────────────────────

    def _seed_positions(self, n: int):
        self._positions = [self._blank_position(i) for i in range(n)]
        self._count_spin.setValue(n)
        self._refresh_table()

    def _blank_position(self, i: int) -> dict:
        return PositionRecord(
            name=f"rack_{i+1}", x=0.0, y=0.0, z=0.0,
            role="Sample", layout="rack",
        ).to_dict()

    def set_group_count(self, n: int):
        current = len(self._positions)
        if n > current:
            for i in range(current, n):
                self._positions.append(self._blank_position(i))
        elif n < current:
            self._positions = self._positions[:n]
        self._refresh_table()

    def _apply_coordinates(self):
        start   = self._start_x.value()
        spacing = self._spacing.value()
        y_val   = self._fixed_y.value()
        z_val   = self._fixed_z.value()
        saved_rows = self._selected_rows()
        for i, pos in enumerate(self._positions):
            pos["x"] = round(start + i * spacing, 4)
            pos["y"] = round(y_val, 4)
            pos["z"] = round(z_val, 4)
        self._refresh_table()
        self._restore_selection(saved_rows, saved_rows[0] if saved_rows else 0)

    def _refresh_table(self):
        self._updating = True
        self._table.setRowCount(len(self._positions))
        for r, pos in enumerate(self._positions):
            for c, field in enumerate(POSITION_FIELDS):
                val = pos.get(field, "")
                if field == "role":
                    combo = QComboBox()
                    combo.addItems(ROLE_PRESETS)
                    if str(val) in ROLE_PRESETS:
                        combo.setCurrentText(str(val))
                    combo.currentTextChanged.connect(
                        lambda v, row=r: self._set_rows_role([row], v)
                    )
                    self._table.setCellWidget(r, c, combo)
                else:
                    item = QTableWidgetItem(str(val))
                    self._table.setItem(r, c, item)
        self._canvas.set_positions(self._positions)
        self._updating = False

    def _table_item_changed(self, item):
        if self._updating:
            return
        r     = item.row()
        c     = item.column()
        field = POSITION_FIELDS[c]
        if r >= len(self._positions):
            return
        val = item.text()
        if field in NUMERIC_FIELDS:
            val = _flt(val)
        self._positions[r][field] = val
        self._canvas.set_positions(self._positions)

    def _table_selection_changed(self, _sel, _desel):
        rows = self._selected_rows()
        if rows:
            self._load_editor_for_row(rows[0])
            self._canvas.selected_index = rows[0]
            self._canvas.update()

    def _selected_rows(self) -> list:
        return sorted(set(idx.row() for idx in self._table.selectionModel().selectedRows()))

    def _select_row(self, row: int):
        self._table.selectRow(row)
        self._load_editor_for_row(row)

    def _load_editor_for_row(self, row: int):
        if row < 0 or row >= len(self._positions):
            return
        pos = self._positions[row]
        self._set_editor_values(
            name=str(pos.get("name", "")),
            x=float(pos.get("x", 0)), y=float(pos.get("y", 0)), z=float(pos.get("z", 0)),
            role=str(pos.get("role", "Sample")),
            group=str(pos.get("group", "")),
            solvent_group=str(pos.get("solvent_group", "")),
            note=str(pos.get("note", "")),
        )

    def _set_editor_values(self, *, name="", x=0.0, y=0.0, z=0.0,
                           role="Sample", group="", solvent_group="", note=""):
        self._ed_name.setText(name)
        self._ed_x.setValue(x)
        self._ed_y.setValue(y)
        self._ed_z.setValue(z)
        if role in ROLE_PRESETS:
            self._ed_role.setCurrentText(role)
        self._ed_group.setText(group)
        self._ed_solvent_group.setText(solvent_group)
        self._ed_note.setText(note)

    def _apply_editor(self):
        rows = self._selected_rows()
        if not rows:
            return
        saved = rows[:]
        for r in rows:
            if r >= len(self._positions):
                continue
            pos = self._positions[r]
            pos["name"]          = self._ed_name.text().strip()
            pos["x"]             = self._ed_x.value()
            pos["y"]             = self._ed_y.value()
            pos["z"]             = self._ed_z.value()
            pos["role"]          = self._ed_role.currentText()
            pos["group"]         = self._ed_group.text().strip()
            pos["solvent_group"] = self._ed_solvent_group.text().strip()
            pos["note"]          = self._ed_note.text().strip()
        self._refresh_table()
        self._restore_selection(saved, saved[0])

    def _set_selected_role(self, role: str):
        rows = self._selected_rows()
        self._set_rows_role(rows, role)

    def _set_rows_role(self, rows: list, role: str):
        saved = self._selected_rows()
        for r in rows:
            if r < len(self._positions):
                self._positions[r]["role"] = role
        self._refresh_table()
        self._restore_selection(saved, saved[0] if saved else 0)

    def _restore_selection(self, rows: list, primary: int):
        sm = self._table.selectionModel()
        sm.clearSelection()
        for r in rows:
            idx = self._table.model().index(r, 0)
            sm.select(
                idx,
                QItemSelectionModel.SelectionFlag.Select |
                QItemSelectionModel.SelectionFlag.Rows,
            )
        if 0 <= primary < self._table.rowCount():
            self._table.scrollToItem(self._table.item(primary, 0))

    def _sync_from_table(self):
        self._updating = True
        for r in range(self._table.rowCount()):
            if r >= len(self._positions):
                break
            pos = self._positions[r]
            for c, field in enumerate(POSITION_FIELDS):
                if field == "role":
                    w = self._table.cellWidget(r, c)
                    if w:
                        pos[field] = w.currentText()
                else:
                    item = self._table.item(r, c)
                    if item:
                        val = item.text()
                        pos[field] = _flt(val) if field in NUMERIC_FIELDS else val
        self._updating = False

    @property
    def result_positions(self) -> list:
        self._sync_from_table()
        return normalize_positions(self._positions)
