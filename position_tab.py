# position_tab.py — SamplePositionTab widget: position table, map, file I/O, and path interpolation.
# Debug entry point: check _refresh_table() combo signal wiring and _on_rows_moved() insert_at math.
# External deps: none (spline_interpolator; station motor RBV read via station ref).

import os
import copy

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QCheckBox, QFileDialog, QMessageBox, QSplitter, QAbstractItemView,
    QDialog, QDialogButtonBox, QFrame, QTableWidget, QTableWidgetItem,
    QComboBox, QSpinBox, QDoubleSpinBox, QFormLayout, QMenu, QToolButton,
    QStyledItemDelegate,
)
from PyQt6.QtCore import (
    Qt, pyqtSignal, QEvent, QItemSelectionModel,
)
from PyQt6.QtGui import QKeySequence, QShortcut

from position_models import POSITION_FIELDS, NUMERIC_FIELDS, ROLE_PRESETS, PositionRecord

_ROLE_COL = POSITION_FIELDS.index("role")
_UNDO_LIMIT_LARGE = 5   # shallow undo history when list is large
_LARGE_THRESHOLD  = 1000


class _RoleDelegate(QStyledItemDelegate):
    """Shows a QComboBox only when a role cell is being edited (not permanently)."""

    def createEditor(self, parent, option, index):
        combo = QComboBox(parent)
        combo.addItems(ROLE_PRESETS)
        combo.setAutoFillBackground(True)
        return combo

    def setEditorData(self, editor, index):
        editor.setCurrentText(index.data(Qt.ItemDataRole.DisplayRole) or "")

    def setModelData(self, editor, model, index):
        model.setData(index, editor.currentText(), Qt.ItemDataRole.EditRole)

    def updateEditorGeometry(self, editor, option, index):
        editor.setGeometry(option.rect)
from position_io import (
    normalize_positions, blank_position, load_positions, save_positions,
    export_bluesky_csv, export_bluesky_csv_split, export_reducer_pairs_csv, _flt,
)
from position_map_widget import PositionMapWidget
from position_rack_builder import RackBuilderDialog
from spline_interpolator import catmull_rom_resample

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
        self._axis_names: dict = {"x": "x", "y": "y", "z": "z"}  # display names for x/y/z cols
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
        limit = _UNDO_LIMIT_LARGE if len(self._positions) > _LARGE_THRESHOLD else self._MAX_UNDO
        if len(self._undo_stack) > limit:
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

        # ── File dropdown ──────────────────────────────────────────────────
        file_btn = QToolButton()
        file_btn.setText("File ▾")
        file_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        file_menu = QMenu(file_btn)
        file_menu.addAction("New",      self._new)
        file_menu.addAction("Open…",    self._open)
        file_menu.addSeparator()
        file_menu.addAction("Save",     self._save)
        file_menu.addAction("Save As…", self._save_as)
        file_menu.addSeparator()
        file_menu.addAction("Export Bluesky CSV",         self._export_bluesky)
        file_menu.addAction("Export Bluesky CSV (Split)", self._export_bluesky_split)
        file_menu.addAction("Export Reducer Pairs",       self._export_reducer)
        file_btn.setMenu(file_menu)
        top.addWidget(file_btn)

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
        self.template_combo.addItems(["Freeform", "Capillary Linear", "Rack Builder", "Chip", "Grid Scan"])
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

        row_bar.addWidget(QLabel("Spacing (mm):"))
        self.interp_edit = QLineEdit("1.0")
        self.interp_edit.setFixedWidth(55)
        row_bar.addWidget(self.interp_edit)

        self.interp_btn = QPushButton("⚙ Interpolate")
        self.interp_btn.setObjectName("actionBtn")
        self.interp_btn.clicked.connect(self._run_interpolate)
        row_bar.addWidget(self.interp_btn)

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
        self.table.setItemDelegateForColumn(_ROLE_COL, _RoleDelegate(self.table))
        self.table.itemChanged.connect(self._item_changed)
        self.table.selectionModel().selectionChanged.connect(self._selection_changed)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._table_context_menu)
        splitter.addWidget(self.table)

        self.map_widget = PositionMapWidget()
        self.map_widget.pointSelected.connect(self._select_row)
        self.map_widget.pointsSelected.connect(self._select_rows_from_map)
        self.map_widget.pointAddRequested.connect(self._add_from_map)
        self.map_widget.moveRequested.connect(self._move_to_position)
        splitter.addWidget(self.map_widget)

        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        main.addWidget(splitter, 1)

    # ── Public API ─────────────────────────────────────────────────────────

    def set_axis_names(self, x_name: str, y_name: str, z_name: str):
        """Update display names for x/y/z columns (e.g. 's_x', 's_y', 's_z')."""
        self._axis_names = {"x": x_name, "y": y_name, "z": z_name}
        labels = [self._axis_names.get(f, f) for f in POSITION_FIELDS]
        self.table.setHorizontalHeaderLabels(labels)

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
        save_positions(path, self.positions(), axis_names=self._axis_names)
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

    # ── Path interpolation ─────────────────────────────────────────────────

    def _run_interpolate(self):
        if not self._positions:
            QMessageBox.warning(self, "Interpolate", "Add at least 2 positions first.")
            return
        try:
            spacing = float(self.interp_edit.text())
            if spacing <= 0:
                raise ValueError
        except ValueError:
            QMessageBox.warning(self, "Interpolate", "Enter a positive number for spacing.")
            return

        try:
            result = catmull_rom_resample(self._positions, spacing)
        except Exception as e:
            QMessageBox.critical(self, "Interpolate Error", str(e))
            return

        if not result:
            QMessageBox.warning(self, "Interpolate", "No points generated.")
            return

        self._apply_interpolation_result(result)

    def _apply_interpolation_result(self, result):
        reply = QMessageBox.question(
            self, "Interpolation Result",
            f"Generated {len(result)} interpolated points.\n"
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
        self.table.setUpdatesEnabled(False)
        self.table.setRowCount(len(self._positions))
        for r, pos in enumerate(self._positions):
            for c, field in enumerate(POSITION_FIELDS):
                item = QTableWidgetItem(str(pos.get(field, "")))
                self.table.setItem(r, c, item)
        self.table.setUpdatesEnabled(True)
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
        if field == "role":
            # Apply to all selected rows when multiple are highlighted
            rows_to_update = self._pending_role_rows if self._pending_role_rows else [row]
            self._push_undo()
            for r in rows_to_update:
                if r < len(self._positions):
                    self._positions[r]["role"] = val
            self._pending_role_rows = []
            self._commit()
            return
        if field in NUMERIC_FIELDS:
            val = _flt(val)
        self._push_undo()
        self._positions[row][field] = val
        self._commit(selected_row=row)

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

    def _select_rows_from_map(self, rows: list):
        sm = self.table.selectionModel()
        sm.clearSelection()
        for r in rows:
            idx = self.table.model().index(r, 0)
            sm.select(idx, QItemSelectionModel.SelectionFlag.Select |
                          QItemSelectionModel.SelectionFlag.Rows)
        if rows:
            self.table.scrollToItem(self.table.item(rows[0], 0))
            self.map_widget.set_selected_row(rows[0])

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

    def _export_bluesky_split(self):
        positions = self.positions()
        if not positions:
            QMessageBox.warning(self, "No Positions", "No positions to export.")
            return

        # Ask for chunk size
        dlg = QDialog(self)
        dlg.setWindowTitle("Export Bluesky CSV — Split")
        form = QFormLayout(dlg)

        total_lbl = QLabel(f"Total points: {len(positions)}")
        form.addRow(total_lbl)

        chunk_spin = QSpinBox()
        chunk_spin.setRange(10, 10000)
        chunk_spin.setValue(200)
        chunk_spin.setSuffix(" pts / plan")
        form.addRow("Points per plan:", chunk_spin)

        preview_lbl = QLabel()
        form.addRow("Plans to create:", preview_lbl)

        def _update():
            import math
            n = math.ceil(len(positions) / chunk_spin.value())
            preview_lbl.setText(str(n))
        chunk_spin.valueChanged.connect(_update)
        _update()

        bbox = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bbox.accepted.connect(dlg.accept)
        bbox.rejected.connect(dlg.reject)
        form.addRow(bbox)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        path, _ = QFileDialog.getSaveFileName(
            self, "Export Bluesky CSV (Split) — choose base filename", "", "CSV (*.csv)")
        if not path:
            return

        try:
            n_files = export_bluesky_csv_split(path, positions, chunk_spin.value())
            import os
            stem = os.path.splitext(os.path.basename(path))[0]
            QMessageBox.information(self, "Export Complete",
                f"Exported {len(positions)} points as {n_files} plan files:\n"
                f"{stem}_001.csv … {stem}_{n_files:03d}.csv")
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
        elif tmpl == "Grid Scan":
            result = self._grid_dialog()
            if result is not None:
                if not self._confirm_replace():
                    return
                self.set_positions(result)

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

    def _grid_dialog(self):
        # Read current motor positions as default start coordinates
        cur_x = cur_y = cur_z = 0.0
        try:
            if self._station is not None:
                cur_x = float(self._station.x_motor.rbv_lbl.text())
                cur_y = float(self._station.y_motor.rbv_lbl.text())
                cur_z = float(self._station.z_motor.rbv_lbl.text())
        except (ValueError, AttributeError):
            pass

        dlg = QDialog(self)
        dlg.setWindowTitle("Grid Scan Template")
        form = QFormLayout(dlg)

        def _dspin(lo=-9999, hi=9999, val=0.0, dec=3):
            s = QDoubleSpinBox()
            s.setRange(lo, hi); s.setDecimals(dec); s.setValue(val)
            return s

        x0s = _dspin(val=cur_x);  form.addRow("Start X  — left (mm):", x0s)
        y0s = _dspin(val=cur_y);  form.addRow("Start Y  — top  (mm):", y0s)
        cz  = _dspin(val=cur_z);  form.addRow("Z (mm):", cz)

        form.addRow(QLabel(""))   # spacer

        width  = _dspin(lo=0.001, hi=9999, val=2.0);  form.addRow("Width  X (mm):", width)
        height = _dspin(lo=0.001, hi=9999, val=2.0);  form.addRow("Height Y (mm):", height)
        step_x = _dspin(lo=0.001, hi=9999, val=0.1);  form.addRow("Step X (mm):", step_x)
        step_y = _dspin(lo=0.001, hi=9999, val=0.1);  form.addRow("Step Y (mm):", step_y)

        form.addRow(QLabel(""))

        snake_combo = QComboBox()
        snake_combo.addItems([
            "X-major snake  (rows →, alternate direction)",
            "X-major unidirectional  (rows →, same direction)",
            "Y-major snake  (columns ↓, alternate direction)",
            "Y-major unidirectional  (columns ↓, same direction)",
        ])
        form.addRow("Scan pattern:", snake_combo)

        # live point-count label
        count_lbl = QLabel()
        form.addRow("Points:", count_lbl)

        def _update_count():
            try:
                nx = max(1, round(width.value() / step_x.value()) + 1)
                ny = max(1, round(height.value() / step_y.value()) + 1)
                count_lbl.setText(f"{nx} × {ny} = {nx*ny}")
            except ZeroDivisionError:
                count_lbl.setText("—")

        for w in (width, height, step_x, step_y):
            w.valueChanged.connect(_update_count)
        _update_count()

        bbox = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bbox.accepted.connect(dlg.accept)
        bbox.rejected.connect(dlg.reject)
        form.addRow(bbox)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return None

        nx = max(1, round(width.value() / step_x.value()) + 1)
        ny = max(1, round(height.value() / step_y.value()) + 1)
        x0 = x0s.value()
        y0 = y0s.value()
        dx = width.value()  / (nx - 1) if nx > 1 else 0.0
        dy = height.value() / (ny - 1) if ny > 1 else 0.0
        z  = cz.value()
        pattern = snake_combo.currentText()

        result = []
        n = 0
        if pattern.startswith("X-major"):
            snake = "snake" in pattern
            for j in range(ny):
                y = y0 + j * dy
                xs_row = (range(nx - 1, -1, -1) if (snake and j % 2 == 1)
                          else range(nx))
                for i in xs_row:
                    x = x0 + i * dx
                    result.append(PositionRecord(
                        name=f"g{n+1:04d}", x=round(x, 6), y=round(y, 6), z=round(z, 6),
                        role="Sample", layout="grid_scan",
                    ).to_dict())
                    n += 1
        else:
            snake = "snake" in pattern
            for i in range(nx):
                x = x0 + i * dx
                ys_col = (range(ny - 1, -1, -1) if (snake and i % 2 == 1)
                          else range(ny))
                for j in ys_col:
                    y = y0 + j * dy
                    result.append(PositionRecord(
                        name=f"g{n+1:04d}", x=round(x, 6), y=round(y, 6), z=round(z, 6),
                        role="Sample", layout="grid_scan",
                    ).to_dict())
                    n += 1
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
