# position_tab.py — SamplePositionTab widget: position table, map, file I/O, and Blender launcher.
# Debug entry point: check _refresh_table() combo signal wiring and _on_rows_moved() insert_at math.
# External deps: none (Blender SSH fired via _BlenderWorker; station motor RBV read via station ref).

import os
import copy

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QCheckBox, QFileDialog, QMessageBox, QSplitter, QAbstractItemView,
    QDialog, QDialogButtonBox, QFrame, QTableWidget, QTableWidgetItem,
    QComboBox, QSpinBox, QDoubleSpinBox, QFormLayout, QMenu,
)
from PyQt6.QtCore import (
    Qt, pyqtSignal, QEvent, QItemSelectionModel, QThread,
)
from PyQt6.QtGui import QKeySequence, QShortcut

from position_models import POSITION_FIELDS, NUMERIC_FIELDS, ROLE_PRESETS, PositionRecord
from position_io import (
    normalize_positions, blank_position, load_positions, save_positions,
    export_bluesky_csv, export_reducer_pairs_csv, _flt,
)
from position_map_widget import PositionMapWidget
from position_rack_builder import RackBuilderDialog
from blender_worker import _BlenderWorker

try:
    import paramiko
    PARAMIKO_AVAILABLE = True
except ImportError:
    PARAMIKO_AVAILABLE = False

_DIR = os.path.dirname(os.path.abspath(__file__))


class SamplePositionTab(QWidget):
    positionsChanged = pyqtSignal(list)

    def __init__(self, station=None, parent=None):
        super().__init__(parent)
        self._station          = station
        self._positions        = []
        self._updating         = False
        self._current_path     = None
        self._pending_role_rows: list[int] = []
        self._undo_stack: list  = []   # snapshots of _positions before each mutation
        self._redo_stack: list  = []
        self._drag_source_row: int = -1  # row captured on mouse-press for manual drag
        self._build_ui()
        self._setup_undo_shortcuts()
        self.set_positions([])  # start empty; use Templates menu or Capture to add positions

    # ── Undo / Redo ────────────────────────────────────────────────────────

    _MAX_UNDO = 50

    def _push_undo(self):
        """Save current positions to undo stack before a mutation. Clears redo stack."""
        snapshot = copy.deepcopy(self._positions)
        # skip if nothing changed since last snapshot
        if self._undo_stack and self._undo_stack[-1] == snapshot:
            return
        self._undo_stack.append(snapshot)
        if len(self._undo_stack) > self._MAX_UNDO:
            self._undo_stack.pop(0)
        self._redo_stack.clear()

    def undo(self):
        if not self._undo_stack:
            return
        self._redo_stack.append(copy.deepcopy(self._positions))
        self.set_positions(self._undo_stack.pop())

    def redo(self):
        if not self._redo_stack:
            return
        self._undo_stack.append(copy.deepcopy(self._positions))
        self.set_positions(self._redo_stack.pop())

    def _setup_undo_shortcuts(self):
        QShortcut(QKeySequence("Ctrl+Z"), self).activated.connect(self.undo)
        QShortcut(QKeySequence("Ctrl+Y"), self).activated.connect(self.redo)
        QShortcut(QKeySequence("Ctrl+Shift+Z"), self).activated.connect(self.redo)

    # ── UI construction ────────────────────────────────────────────────────

    def _build_ui(self):
        main = QVBoxLayout(self)
        main.setContentsMargins(4, 4, 4, 4)
        main.setSpacing(4)

        # ── Top toolbar (file + capture + template + view) ──────────────
        top = QHBoxLayout()
        top.setSpacing(4)

        new_btn = QPushButton("New")
        new_btn.clicked.connect(self._new)
        top.addWidget(new_btn)

        open_btn = QPushButton("Open…")
        open_btn.clicked.connect(self._open)
        top.addWidget(open_btn)

        save_btn = QPushButton("Save")
        save_btn.clicked.connect(self._save)
        top.addWidget(save_btn)

        saveas_btn = QPushButton("Save As…")
        saveas_btn.clicked.connect(self._save_as)
        top.addWidget(saveas_btn)

        exp_bs_btn = QPushButton("Export Bluesky CSV")
        exp_bs_btn.clicked.connect(self._export_bluesky)
        top.addWidget(exp_bs_btn)

        exp_rd_btn = QPushButton("Export Reducer Pairs")
        exp_rd_btn.clicked.connect(self._export_reducer)
        top.addWidget(exp_rd_btn)

        sep0 = QFrame(); sep0.setFrameShape(QFrame.Shape.VLine)
        sep0.setObjectName("toolSep"); top.addWidget(sep0)

        self.capture_btn = QPushButton("📍 Capture from Stage")
        self.capture_btn.setObjectName("captureBtn")
        self.capture_btn.setToolTip("Read current motor positions and add entry")
        self.capture_btn.clicked.connect(self._capture_from_stage)
        top.addWidget(self.capture_btn)

        sep1 = QFrame(); sep1.setFrameShape(QFrame.Shape.VLine)
        sep1.setObjectName("toolSep"); top.addWidget(sep1)

        top.addWidget(QLabel("Template:"))
        self.template_combo = QComboBox()
        self.template_combo.addItems(["Freeform", "Capillary Linear", "Rack Builder", "Chip"])
        top.addWidget(self.template_combo)

        apply_tmpl_btn = QPushButton("Apply")
        apply_tmpl_btn.clicked.connect(self._apply_template)
        top.addWidget(apply_tmpl_btn)

        sep2 = QFrame(); sep2.setFrameShape(QFrame.Shape.VLine)
        sep2.setObjectName("toolSep"); top.addWidget(sep2)

        self.show_arrows_cb = QCheckBox("Arrows")
        self.show_arrows_cb.setChecked(False)
        self.show_arrows_cb.toggled.connect(
            lambda v: self.map_widget.set_sequence_arrows_visible(v))
        top.addWidget(self.show_arrows_cb)

        self.show_names_cb = QCheckBox("Names")
        self.show_names_cb.setChecked(True)
        self.show_names_cb.toggled.connect(
            lambda v: self.map_widget.set_names_visible(v))
        top.addWidget(self.show_names_cb)

        self.click_add_cb = QCheckBox("Click-add")
        self.click_add_cb.setChecked(False)
        self.click_add_cb.toggled.connect(
            lambda v: self.map_widget.set_add_points_enabled(v))
        top.addWidget(self.click_add_cb)

        top.addStretch()
        main.addLayout(top)

        # ── Row management bar ──────────────────────────────────────────
        row_bar = QHBoxLayout()
        row_bar.setSpacing(4)

        add_btn = QPushButton("＋ Add")
        add_btn.setObjectName("greenBtn")
        add_btn.clicked.connect(self._add_row)
        row_bar.addWidget(add_btn)

        del_btn = QPushButton("－ Delete")
        del_btn.setObjectName("redBtn")
        del_btn.clicked.connect(self._delete_selected)
        row_bar.addWidget(del_btn)

        dup_btn = QPushButton("Duplicate")
        dup_btn.clicked.connect(self._duplicate_selected)
        row_bar.addWidget(dup_btn)

        row_bar.addSpacing(8)
        row_bar.addWidget(QLabel("Selected role:"))
        self.bulk_role_combo = QComboBox()
        self.bulk_role_combo.addItems(ROLE_PRESETS)
        row_bar.addWidget(self.bulk_role_combo)

        assign_btn = QPushButton("Assign")
        assign_btn.clicked.connect(self._assign_role)
        row_bar.addWidget(assign_btn)

        sep3 = QFrame(); sep3.setFrameShape(QFrame.Shape.VLine)
        sep3.setObjectName("toolSep"); row_bar.addWidget(sep3)

        row_bar.addWidget(QLabel("Blender spacing (mm):"))
        self.interp_edit = QLineEdit("1.0")
        self.interp_edit.setFixedWidth(55)
        row_bar.addWidget(self.interp_edit)

        self.blender_btn = QPushButton("⚙ Run Blender")
        self.blender_btn.setObjectName("actionBtn")
        self.blender_btn.clicked.connect(self._run_blender)
        row_bar.addWidget(self.blender_btn)

        row_bar.addStretch()
        main.addLayout(row_bar)

        # ── Splitter: table | map ───────────────────────────────────────
        splitter = QSplitter(Qt.Orientation.Horizontal)

        self.table = QTableWidget(0, len(POSITION_FIELDS))
        self.table.setHorizontalHeaderLabels(POSITION_FIELDS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.installEventFilter(self)
        self.table.setDragEnabled(True)
        self.table.setAcceptDrops(True)
        self.table.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.table.setDropIndicatorShown(True)
        self.table.setDragDropOverwriteMode(False)
        self.table.viewport().installEventFilter(self)  # captures source row + handles Drop
        self.table.itemChanged.connect(self._item_changed)
        self.table.selectionModel().selectionChanged.connect(self._selection_changed)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._table_context_menu)
        splitter.addWidget(self.table)

        self.map_widget = PositionMapWidget()
        self.map_widget.pointSelected.connect(self._select_row)
        self.map_widget.pointAddRequested.connect(self._add_from_map)
        self.map_widget.moveRequested.connect(self._move_to_position)
        splitter.addWidget(self.map_widget)

        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        main.addWidget(splitter, 1)

    # ── Public API ─────────────────────────────────────────────────────────

    def positions(self) -> list:
        return normalize_positions(self._positions)

    def set_positions(self, positions):
        self._positions = normalize_positions(positions)
        self._refresh_table()
        self.map_widget.set_positions(self._positions)
        if self._positions:
            self.table.selectRow(0)
        self.positionsChanged.emit(self.positions())

    def load_positions(self, path):
        from pathlib import Path
        self.set_positions(load_positions(path))
        self._current_path = Path(path)

    def save_positions(self, path):
        from pathlib import Path
        save_positions(path, self.positions())
        self._current_path = Path(path)

    # ── Capture from stage ─────────────────────────────────────────────────

    def _capture_from_stage(self):
        if self._station is None:
            QMessageBox.warning(self, "No Station", "Not connected to station.")
            return
        try:
            x = float(self._station.x_motor.rbv_lbl.text())
            y = float(self._station.y_motor.rbv_lbl.text())
            z = float(self._station.z_motor.rbv_lbl.text())
        except (ValueError, AttributeError):
            QMessageBox.warning(self, "No Readback", "Motor RBV not available.")
            return
        self._push_undo()
        rows = self._selected_rows()
        idx  = rows[-1] + 1 if rows else len(self._positions)
        self._positions.insert(idx, PositionRecord(
            name=f"pos_{idx+1}", x=x, y=y, z=z,
            role="Sample", layout="freeform",
        ).to_dict())
        self._positions = normalize_positions(self._positions)
        self.set_positions(self._positions)
        self._select_row(idx)

    # ── Blender ────────────────────────────────────────────────────────────

    def _run_blender(self):
        if self._station is None:
            QMessageBox.warning(self, "Blender", "No station connected.")
            return
        if not PARAMIKO_AVAILABLE:
            QMessageBox.critical(self, "SSH Error",
                                 "paramiko is not installed.\nRun: pip install paramiko")
            return
        try:
            spacing = float(self.interp_edit.text())
        except ValueError:
            QMessageBox.warning(self, "Blender", "Enter a valid number for spacing.")
            return

        data_dir = os.path.join(_DIR, "Data")
        os.makedirs(data_dir, exist_ok=True)
        self.blender_btn.setEnabled(False)
        self.blender_btn.setText("Running…")

        worker = _BlenderWorker(self._station.cfg, self._positions, spacing, data_dir)
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(lambda result: self._station._on_blender_done(result, thread))
        worker.error.connect(lambda msg: self._station._on_blender_error(msg, thread))
        thread.start()

    def _apply_blender_result(self, result):
        reply = QMessageBox.question(
            self, "Blender Result",
            f"Blender returned {len(result)} interpolated points.\n"
            "Yes = Append to existing  |  No = Replace",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No |
            QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Yes,
        )
        if reply == QMessageBox.StandardButton.Cancel:
            return
        interp = [
            PositionRecord(
                name=f"interp_{i+1}",
                x=float(r.get("x", 0)), y=float(r.get("y", 0)), z=float(r.get("z", 0)),
                role="Interpolated", layout="blender_interpolated",
            ).to_dict()
            for i, r in enumerate(result)
        ]
        new = (self._positions + interp) if reply == QMessageBox.StandardButton.Yes else interp
        self.set_positions(new)

    # ── Table management ───────────────────────────────────────────────────

    def _refresh_table(self):
        self._updating = True
        self.table.setRowCount(len(self._positions))
        for r, pos in enumerate(self._positions):
            for c, field in enumerate(POSITION_FIELDS):
                val = pos.get(field, "")
                if field == "role":
                    combo = QComboBox()
                    combo.addItems(ROLE_PRESETS)
                    if str(val) in ROLE_PRESETS:
                        combo.setCurrentText(str(val))
                    combo.currentTextChanged.connect(
                        lambda v, row=r: self._role_changed(row, v)
                    )
                    self.table.setCellWidget(r, c, combo)
                else:
                    item = QTableWidgetItem(str(val))
                    self.table.setItem(r, c, item)
        self._updating = False

    def eventFilter(self, watched, event):
        if watched is self.table:
            if event.type() == QEvent.Type.KeyPress:
                if event.key() == Qt.Key.Key_Delete:
                    self._delete_selected()
                    return True
            if event.type() in (QEvent.Type.FocusIn, QEvent.Type.MouseButtonPress):
                self._pending_role_rows = self._selected_rows()
        if watched is self.table.viewport():
            if event.type() == QEvent.Type.MouseButtonPress:
                # capture source row before drag starts (currentRow() is unreliable at Drop time)
                idx = self.table.indexAt(event.pos())
                self._drag_source_row = idx.row() if idx.isValid() else -1
            elif event.type() == QEvent.Type.Drop:
                # Handle row reorder ourselves — Qt's InternalMove corrupts cell data.
                # Return True to consume the event so Qt never touches the model.
                src = self._drag_source_row
                dst = self.table.rowAt(int(event.position().y()))
                if dst < 0:
                    dst = len(self._positions) - 1
                if src >= 0 and src != dst and src < len(self._positions):
                    self._push_undo()
                    item = self._positions.pop(src)
                    self._positions.insert(dst, item)
                    self._refresh_table()
                    self._select_row(dst)
                    self.map_widget.set_positions(self._positions)
                    self.positionsChanged.emit(self.positions())
                self._drag_source_row = -1
                return True  # consume — do NOT let Qt process this drop
        return super().eventFilter(watched, event)

    def _item_changed(self, item):
        if self._updating:
            return
        row   = item.row()
        col   = item.column()
        if row >= len(self._positions):
            return
        field = POSITION_FIELDS[col]
        val   = item.text()
        if field in NUMERIC_FIELDS:
            val = _flt(val)
        self._push_undo()
        self._positions[row][field] = val
        self._commit(selected_row=row)

    def _role_changed(self, row: int, value: str):
        if self._updating:
            return
        rows_to_update = self._pending_role_rows if self._pending_role_rows else [row]
        self._push_undo()
        for r in rows_to_update:
            if r < len(self._positions):
                self._positions[r]["role"] = value
        self._pending_role_rows = []
        self._commit()

    def _commit(self, selected_row: int | None = None):
        self._positions = normalize_positions(self._positions)
        self.map_widget.set_positions(self._positions)
        if selected_row is not None:
            self.map_widget.set_selected_row(selected_row)
        self.positionsChanged.emit(self.positions())

    def _selection_changed(self):
        rows = self._selected_rows()
        if rows:
            self.map_widget.set_selected_row(rows[0])
        self._pending_role_rows = rows

    def _select_row(self, row: int):
        self.table.selectRow(row)
        self.map_widget.set_selected_row(row)

    def _selected_rows(self) -> list:
        return sorted(set(idx.row() for idx in self.table.selectionModel().selectedRows()))

    def _assign_role(self):
        role = self.bulk_role_combo.currentText()
        rows = self._selected_rows()
        self._push_undo()
        for r in rows:
            self._positions[r]["role"] = role
        saved = rows[:]
        self._refresh_table()
        self._restore_selection(saved, saved[0] if saved else 0)
        self.positionsChanged.emit(self.positions())

    # ── Row operations ─────────────────────────────────────────────────────

    def _add_row(self):
        self._push_undo()
        rows = self._selected_rows()
        idx  = rows[-1] + 1 if rows else len(self._positions)
        self._positions.insert(idx, blank_position(idx))
        # renumber names
        self._positions = normalize_positions(self._positions)
        self.set_positions(self._positions)
        self._select_row(idx)

    def _add_from_map(self, x: float, y: float):
        self._push_undo()
        rows = self._selected_rows()
        z    = float(self._positions[rows[0]].get("z", 0)) if rows else 0.0
        idx  = len(self._positions)
        self._positions.append(PositionRecord(
            name=f"pos_{idx+1}", x=x, y=y, z=z,
            role="Sample", layout="freeform",
        ).to_dict())
        self.set_positions(self._positions)
        self._select_row(idx)

    def _move_to_position(self, row: int):
        """Move X/Y motors to the position at the given row index."""
        if self._station is None:
            QMessageBox.warning(self, "No Station", "Not connected to station.")
            return
        if row < 0 or row >= len(self._positions):
            return
        pos = self._positions[row]
        self._station.x_motor.move_to(float(pos.get("x", 0)))
        self._station.y_motor.move_to(float(pos.get("y", 0)))

    def _table_context_menu(self, point):
        """Right-click context menu on the position table."""
        row = self.table.rowAt(point.y())
        if row < 0:
            return
        menu = QMenu(self)
        act_move = menu.addAction("Move to Position")
        if menu.exec(self.table.viewport().mapToGlobal(point)) == act_move:
            self._move_to_position(row)

    def _delete_selected(self):
        self._push_undo()
        rows = sorted(self._selected_rows(), reverse=True)
        for r in rows:
            if 0 <= r < len(self._positions):
                self._positions.pop(r)
        self.set_positions(self._positions)

    def _duplicate_selected(self):
        self._push_undo()
        rows = self._selected_rows()
        if not rows:
            return
        r   = rows[-1]
        pos = dict(self._positions[r])
        pos["name"] = str(pos.get("name", "")) + "_copy"
        self._positions.insert(r + 1, pos)
        self.set_positions(self._positions)
        self._select_row(r + 1)

    def _move_selected(self, delta: int):
        self._push_undo()
        rows = self._selected_rows()
        if not rows:
            return
        row = rows[0]
        new = row + delta
        if 0 <= new < len(self._positions):
            self._positions.insert(new, self._positions.pop(row))
            self.set_positions(self._positions)
            self._select_row(new)

    def _on_rows_moved(self, _src_parent, src_start: int, src_end: int,
                       _dst_parent, dst_row: int):
        """Sync self._positions after a drag-and-drop row reorder."""
        self._push_undo()
        moved = [self._positions.pop(src_start) for _ in range(src_end - src_start + 1)]
        insert_at = dst_row if dst_row <= src_start else dst_row - (src_end - src_start + 1)
        for i, p in enumerate(moved):
            self._positions.insert(insert_at + i, p)
        self._refresh_table()   # rebuilds cells from _positions and resets _updating
        self.map_widget.set_positions(self._positions)
        self.positionsChanged.emit(self.positions())

    # ── File operations ────────────────────────────────────────────────────

    def _new(self):
        if self._confirm_replace():
            self.set_positions([])

    def _open(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Positions", "",
            "All supported (*.csv *.json *.pos);;CSV (*.csv);;JSON (*.json);;POS (*.pos)",
        )
        if not path:
            return
        if not self._confirm_replace():
            return
        try:
            self.load_positions(path)
        except Exception as e:
            QMessageBox.critical(self, "Load Error", str(e))

    def _save(self):
        if self._current_path:
            try:
                self.save_positions(str(self._current_path))
            except Exception as e:
                QMessageBox.critical(self, "Save Error", str(e))
        else:
            self._save_as()

    def _save_as(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Positions", "",
            "CSV (*.csv);;JSON (*.json);;POS (*.pos)",
        )
        if not path:
            return
        try:
            self.save_positions(path)
        except Exception as e:
            QMessageBox.critical(self, "Save Error", str(e))

    def _export_bluesky(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export Bluesky CSV", "", "CSV (*.csv)")
        if path:
            try:
                export_bluesky_csv(path, self.positions())
            except Exception as e:
                QMessageBox.critical(self, "Export Error", str(e))

    def _export_reducer(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export Reducer Pairs", "", "CSV (*.csv)")
        if path:
            try:
                export_reducer_pairs_csv(path, self.positions())
            except Exception as e:
                QMessageBox.critical(self, "Export Error", str(e))

    # ── Templates ──────────────────────────────────────────────────────────

    def _apply_template(self):
        tmpl = self.template_combo.currentText()
        if tmpl == "Freeform":
            if not self._confirm_replace():
                return
            self.set_positions([blank_position(i) for i in range(5)])
        elif tmpl == "Capillary Linear":
            result = self._capillary_dialog()
            if result is not None:
                if not self._confirm_replace():
                    return
                self.set_positions(result)
        elif tmpl == "Rack Builder":
            dlg = RackBuilderDialog(self._positions if self._positions else None, parent=self)
            if dlg.exec() == QDialog.DialogCode.Accepted:
                self.set_positions(dlg.result_positions)
        elif tmpl == "Chip":
            if not self._confirm_replace():
                return
            self.set_positions([blank_position(i, layout="chip") for i in range(10)])

    def _capillary_dialog(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("Capillary Linear Template")
        form = QFormLayout(dlg)

        count_spin = QSpinBox()
        count_spin.setRange(1, 200)
        count_spin.setValue(13)
        form.addRow("Count:", count_spin)

        spacing_spin = QDoubleSpinBox()
        spacing_spin.setRange(-999, 999)
        spacing_spin.setDecimals(3)
        spacing_spin.setValue(1.0)
        form.addRow("Spacing (mm):", spacing_spin)

        start_x = QDoubleSpinBox(); start_x.setRange(-9999, 9999); start_x.setDecimals(3)
        start_y = QDoubleSpinBox(); start_y.setRange(-9999, 9999); start_y.setDecimals(3)
        start_z = QDoubleSpinBox(); start_z.setRange(-9999, 9999); start_z.setDecimals(3)
        form.addRow("Start X (mm):", start_x)
        form.addRow("Start Y (mm):", start_y)
        form.addRow("Start Z (mm):", start_z)

        axis_combo = QComboBox()
        axis_combo.addItems(["x", "y"])
        form.addRow("Axis:", axis_combo)

        bbox = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        bbox.accepted.connect(dlg.accept)
        bbox.rejected.connect(dlg.reject)
        form.addRow(bbox)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return None

        n       = count_spin.value()
        spacing = spacing_spin.value()
        sx      = start_x.value()
        sy      = start_y.value()
        sz      = start_z.value()
        axis    = axis_combo.currentText()
        result  = []
        for i in range(n):
            x = sx + (i * spacing if axis == "x" else 0.0)
            y = sy + (i * spacing if axis == "y" else 0.0)
            result.append(PositionRecord(
                name=f"cap_{i+1}", x=x, y=y, z=sz,
                role="Sample", layout="capillary_linear",
            ).to_dict())
        return result

    def _confirm_replace(self) -> bool:
        if not self._positions:
            return True
        reply = QMessageBox.question(
            self, "Replace positions?",
            "This will replace all current positions. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return reply == QMessageBox.StandardButton.Yes

    def _restore_selection(self, rows: list, primary: int):
        sm = self.table.selectionModel()
        sm.clearSelection()
        for r in rows:
            idx = self.table.model().index(r, 0)
            sm.select(
                idx,
                QItemSelectionModel.SelectionFlag.Select |
                QItemSelectionModel.SelectionFlag.Rows,
            )
        if 0 <= primary < self.table.rowCount():
            self.table.scrollToItem(self.table.item(primary, 0))
