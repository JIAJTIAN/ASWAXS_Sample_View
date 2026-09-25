# position_map_widget.py — Interactive 2-D scatter map of sample positions using pyqtgraph.
# Debug entry point: check _redraw() role_groups logic and _scatter_clicked data() index.
# External deps: none.

import pyqtgraph as pg

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QMenu, QGraphicsRectItem
from PyQt6.QtCore import Qt, pyqtSignal, QRectF
from PyQt6.QtGui import QColor

from position_models import ROLE_COLORS


class _SelectableViewBox(pg.ViewBox):
    """ViewBox where left-drag draws a rubber-band selection rect and
    middle-drag pans; left-drag draws rubber-band selection."""

    selectionMade = pyqtSignal(object)   # QRectF in view coords

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setMouseMode(pg.ViewBox.PanMode)  # middle drag → pan via parent
        self._rb_item  = None
        self._rb_start = None

    def mouseDragEvent(self, ev, axis=None):
        if ev.button() != Qt.MouseButton.LeftButton:
            super().mouseDragEvent(ev, axis)
            return
        ev.accept()
        cur = self.mapToView(ev.pos())
        if ev.isStart():
            self._rb_start = self.mapToView(ev.buttonDownPos())
            self._rb_item  = QGraphicsRectItem()
            self._rb_item.setPen(pg.mkPen('#3b82f6', width=1,
                                          style=Qt.PenStyle.DashLine))
            self._rb_item.setBrush(pg.mkBrush(59, 130, 246, 40))
            self._rb_item.setZValue(1e9)
            self.addItem(self._rb_item, ignoreBounds=True)

        if self._rb_start is not None and self._rb_item is not None:
            x0 = min(self._rb_start.x(), cur.x())
            y0 = min(self._rb_start.y(), cur.y())
            x1 = max(self._rb_start.x(), cur.x())
            y1 = max(self._rb_start.y(), cur.y())
            self._rb_item.setRect(QRectF(x0, y0, x1 - x0, y1 - y0))

        if ev.isFinish():
            if self._rb_item is not None:
                self.removeItem(self._rb_item)
                self._rb_item = None
            if self._rb_start is not None:
                x0 = min(self._rb_start.x(), cur.x())
                y0 = min(self._rb_start.y(), cur.y())
                x1 = max(self._rb_start.x(), cur.x())
                y1 = max(self._rb_start.y(), cur.y())
                self._rb_start = None
                if x1 - x0 > 1e-9 or y1 - y0 > 1e-9:
                    self.selectionMade.emit(QRectF(x0, y0, x1 - x0, y1 - y0))


class PositionMapWidget(QWidget):
    pointSelected      = pyqtSignal(int)
    pointsSelected     = pyqtSignal(list)   # list[int] — rubber-band multi-select
    pointAddRequested  = pyqtSignal(float, float)
    moveRequested      = pyqtSignal(int)   # emitted on right-click → Move to Position

    def __init__(self, parent=None):
        super().__init__(parent)
        self._positions        = []
        self._selected_row     = -1
        self._show_arrows      = False
        self._show_names       = True
        self._add_points_enabled = False

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)

        self._vb = _SelectableViewBox()
        self._vb.selectionMade.connect(self._on_rect_selected)

        self.plot = pg.PlotWidget(viewBox=self._vb)
        self.plot.setBackground("w")
        self.plot.setLabel("bottom", "x (mm)")
        self.plot.setLabel("left",   "y (mm)")
        self.plot.showGrid(x=True, y=True, alpha=0.22)
        self._vb.invertY(True)   # +Y goes down, matching stage coordinates
        self.plot.enableAutoRange(False)
        self.plot.setRange(xRange=[-10, 10], yRange=[-10, 10])
        lay.addWidget(self.plot)

        self.plot.scene().sigMouseClicked.connect(self._plot_clicked)

        self._label_items     = []
        self._arrow_items     = []
        self._scatter_items   = []
        self._selection_item  = None

    def set_positions(self, positions):
        self._positions = list(positions)
        self._redraw()

    def set_selected_row(self, row: int):
        self._selected_row = row
        self._draw_selection()

    def set_sequence_arrows_visible(self, v: bool):
        self._show_arrows = v
        self._redraw()

    def set_names_visible(self, v: bool):
        self._show_names = v
        self._redraw()

    def set_add_points_enabled(self, v: bool):
        self._add_points_enabled = v
        if v:
            self.plot.setCursor(Qt.CursorShape.CrossCursor)
        else:
            self.plot.unsetCursor()

    def _redraw(self):
        for item in self._scatter_items + self._label_items + self._arrow_items:
            try:
                self.plot.removeItem(item)
            except Exception:
                pass
        if self._selection_item:
            try:
                self.plot.removeItem(self._selection_item)
            except Exception:
                pass
        self._scatter_items  = []
        self._label_items    = []
        self._arrow_items    = []
        self._selection_item = None

        if not self._positions:
            return

        n_pts = len(self._positions)
        _LABEL_LIMIT = 1000   # suppress text labels above this count
        pt_size = 6 if n_pts > 1000 else 12   # smaller dots for dense grids

        # Group by role
        role_groups: dict[str, dict] = {}
        for i, pos in enumerate(self._positions):
            role = str(pos.get("role", "Sample") or "Sample")
            if role not in role_groups:
                role_groups[role] = {"xs": [], "ys": [], "indices": []}
            role_groups[role]["xs"].append(float(pos.get("x", 0)))
            role_groups[role]["ys"].append(float(pos.get("y", 0)))
            role_groups[role]["indices"].append(i)

        for role, data in role_groups.items():
            hex_color = ROLE_COLORS.get(role, "#888888")
            qc = QColor(hex_color)
            pen = pg.mkPen(None) if n_pts > 1000 else pg.mkPen(qc.darker(140), width=1)
            scatter = pg.ScatterPlotItem(
                x=data["xs"], y=data["ys"],
                size=pt_size,
                brush=pg.mkBrush(qc),
                pen=pen,
                data=data["indices"],
            )
            scatter.sigClicked.connect(self._scatter_clicked)
            self.plot.addItem(scatter)
            self._scatter_items.append(scatter)

        if self._show_names and n_pts <= _LABEL_LIMIT:
            for pos in self._positions:
                name = str(pos.get("name", ""))
                if name:
                    text = pg.TextItem(name, color="#333333", anchor=(0, 1))
                    text.setPos(float(pos.get("x", 0)), float(pos.get("y", 0)))
                    self.plot.addItem(text)
                    self._label_items.append(text)

        if self._show_arrows and n_pts > 1:
            self._draw_sequence_tube()

        self._draw_selection()
        self.plot.autoRange(padding=0.15)

    def _scatter_clicked(self, _scatter, points, ev=None):
        if not points:
            return
        idx = points[0].data()
        if idx is None:
            return
        self.pointSelected.emit(int(idx))

    def _on_rect_selected(self, rect: QRectF):
        """Find all positions inside the rubber-band rect and emit pointsSelected."""
        indices = []
        for i, pos in enumerate(self._positions):
            x = float(pos.get("x", 0))
            y = float(pos.get("y", 0))
            if rect.contains(x, y):
                indices.append(i)
        if indices:
            self.pointsSelected.emit(indices)

    def _plot_clicked(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            self._handle_right_click(event)
            return
        if not self._add_points_enabled:
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        pos    = event.scenePos()
        mapped = self._vb.mapSceneToView(pos)
        self.pointAddRequested.emit(mapped.x(), mapped.y())

    def _handle_right_click(self, event):
        """Right-click on the plot: show context menu if a scatter point is nearby."""
        scene_pos = event.scenePos()
        # hit-test every scatter item
        hit_idx = None
        for scatter in self._scatter_items:
            item_pos = scatter.mapFromScene(scene_pos)
            pts = scatter.pointsAt(item_pos)
            if pts:
                hit_idx = pts[0].data()
                break
        if hit_idx is None:
            return
        menu = QMenu()
        act = menu.addAction("Move to Position")
        if menu.exec(event.screenPos().toPoint()) == act:
            self.moveRequested.emit(int(hit_idx))

    def _draw_selection(self):
        if self._selection_item:
            try:
                self.plot.removeItem(self._selection_item)
            except Exception:
                pass
            self._selection_item = None
        if self._selected_row < 0 or self._selected_row >= len(self._positions):
            return
        pos = self._positions[self._selected_row]
        x   = float(pos.get("x", 0))
        y   = float(pos.get("y", 0))
        self._selection_item = pg.ScatterPlotItem(
            x=[x], y=[y],
            size=22,
            brush=pg.mkBrush(None),
            pen=pg.mkPen("#1e3a5f", width=2),
            symbol='o',
        )
        self.plot.addItem(self._selection_item)

    def _draw_sequence_tube(self):
        # Split positions into contiguous segments of the same layout so that
        # interpolated paths don't draw a connecting line back to the waypoints.
        segments: list[list] = []
        current: list = []
        current_layout = None
        for p in self._positions:
            layout = p.get("layout", "")
            if layout != current_layout and current:
                segments.append(current)
                current = []
            current_layout = layout
            current.append(p)
        if current:
            segments.append(current)

        for seg in segments:
            xs = [float(p.get("x", 0)) for p in seg]
            ys = [float(p.get("y", 0)) for p in seg]
            if len(xs) < 2:
                continue
            outer = pg.PlotCurveItem(xs, ys, pen=pg.mkPen("#93c5fd", width=6))
            inner = pg.PlotCurveItem(xs, ys, pen=pg.mkPen("#3b82f6", width=2))
            self.plot.addItem(outer)
            self.plot.addItem(inner)
            self._arrow_items.extend([outer, inner])
            mxs = [(xs[i - 1] + xs[i]) / 2 for i in range(1, len(xs))]
            mys = [(ys[i - 1] + ys[i]) / 2 for i in range(1, len(ys))]
            midpoints = pg.ScatterPlotItem(
                mxs, mys, size=8,
                brush=pg.mkBrush("#2563eb"),
                pen=pg.mkPen(None),
            )
            self.plot.addItem(midpoints)
            self._arrow_items.append(midpoints)
