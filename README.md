# ASWAXS_Sample_View

**ASWAXS_Sample_View** is the beamline data-collection front-end for the ASWAXS (Anomalous Small- and Wide-Angle X-ray Scattering) instrument at the Advanced Photon Source (APS), Argonne National Laboratory.

It is part of a two-component software stack:

| Component | Role |
|---|---|
| **ASWAXS_Sample_View** (this repo) | Sample positioning, camera viewing, motor control, position list management |
| [FrameByFrame](https://github.com/JIAJTIAN/FrameByFrame) | Live data reduction and visualization pipeline |

---

## Overview

ASWAXS_Sample_View provides a unified GUI for the beamline scientist to:

- View the live sample camera and navigate to sample positions by clicking on the image
- Control X, Y, and Z (focus) motors with real-time EPICS readbacks
- Build and manage a sample position list with rich metadata (role, group, solvent group, layout, notes)
- Visualize the spatial distribution of all positions on an interactive 2D map
- Generate position grids with snake-trail scan patterns
- Smooth-interpolate intermediate positions along a path using a built-in Catmull-Rom spline (no external dependencies)
- Export position lists to CSV / JSON / POS formats for downstream acquisition scripts

---

## Features

### Camera Tab
- **Live camera stream** via EPICS area detector (OpenCV display with pyqtgraph)
- **Click-to-move**: click any point on the image to drive X/Y motors to that location (uses ROI center as beam reference; requires pixel-to-mm calibration)
- **Center ROI** button: resets the ROI to the image center
- **Autofocus**: runs an external autofocus script against the Z motor
- **Auto-add / manual-add to list**: record moves as sample positions automatically or on demand

### Motor Control Bar
- Compact panel for **X, Y, Z motors** showing:
  - EPICS DESC, RBV (live readback), SP (setpoint), MOVN indicator
  - Step size + tweak ◀▶ buttons for manual jog
- Motor PVs and axis display names are configurable via the Setup dialog

### Sample Positions Tab
- **Position table** with columns: `name`, `x` / `y` / `z` (displayed with motor axis names, e.g. `s_x`), `role`, `layout`, `group`, `solvent_group`, `note`
- **Role presets**: Sample, Solvent, GC, Air, Empty, Standard, Background, Inlet, Outlet, Channel, Observation, Skip — each with a distinct color on the map
- **Drag-and-drop row reordering** (full custom implementation — no Qt InternalMove corruption)
- **Bulk role assignment** to multiple selected rows
- **Undo / Redo** (Ctrl+Z / Ctrl+Y / Ctrl+Shift+Z) for all list operations, up to 50 levels
- **Capture from stage**: insert current motor position after the selected row
- **Move to position**: right-click any row or map point → "Move to Position"
- **2D position map**: interactive scatter plot with color-coded roles, optional sequence arrows and name labels; click-add mode
- **File I/O**: open/save as CSV, JSON, or `.pos`; backward-compatible column alias resolution (`s_x` → `x`, etc.)
- **Export**: Bluesky CSV and Reducer Pairs CSV formats

#### Position Generation Templates

| Template | Description |
|---|---|
| Freeform | 5 blank rows |
| Capillary Linear | N evenly spaced positions along X or Y |
| Rack Builder | Interactive rack/well-plate layout builder |
| Chip | 10 blank positions with chip layout |
| **Grid Scan** | Snake-trail raster scan over a rectangular region |

**Grid Scan dialog parameters:**
- Center X / Y (mm), Z (mm)
- Width / Height (mm) — total extent of the region
- Step X / Step Y (mm) — point spacing
- Snake direction: **X-major** (scan rows left↔right, step in Y) or **Y-major** (scan columns up↔down, step in X)
- Live point count preview (e.g. `21 × 11 = 231`)

#### Path Interpolation
- **⚙ Interpolate** button: fits a **centripetal Catmull-Rom spline** through the current waypoints and resamples at the requested arc-length spacing
- Straight channel sections remain straight; curved sections produce smooth curves — no external software required
- Output points can be appended to or replace the existing list

### Setup Dialog
- **Motor PVs**: X, Y, Z motor base PV names and display axis names (`s_x`, `s_y`, `s_z`)
- **Camera**: EPICS camera prefix, image prefix, ROI prefix, autofocus step size

Settings are persisted to `sample_station_config.json` alongside the script.

### Menu Bar

| Menu | Actions |
|---|---|
| **File** | New, Open, Save, Save As, Export |
| **Acquisition** | Capture Position, Move to Selected |
| **Positions** | Add, Delete, Duplicate, Move Up/Down, Assign Role, Clear All |
| **Setup** | Open Setup Dialog |
| **Help** | About |

---

## Installation

### pip — editable install (recommended)

Editable install keeps `sample_station_config.json` and the calibration file in the
source directory where you expect them, and `aswaxs-station` works from any directory.

```bash
git clone https://github.com/JIAJTIAN/ASWAXS_Sample_View.git
cd ASWAXS_Sample_View
pip install -e .
```

Then launch from anywhere:

```bash
aswaxs-station
```

### Manual / conda (beamline)

Install in the `pydm-env` conda environment:

```bash
pip install PyQt6 pyqtgraph numpy pyepics
python sample_station.py
```

### Dependencies

```
Python >= 3.10
PyQt6 >= 6.4
pyqtgraph >= 0.13
numpy
pyepics          # optional — EPICS motor/PV control
opencv-python    # optional — camera streaming
```

### Offline mode

The application runs without EPICS or a camera connected. All hardware-dependent features degrade gracefully — the GUI remains fully functional for position list editing, file I/O, template generation, path interpolation, and map visualization.

---

## Configuration

Settings are stored in `sample_station_config.json` (auto-created on first run):

```json
{
    "X_MOTOR_PV":       "PREFIX:mXX",
    "Y_MOTOR_PV":       "PREFIX:mXX",
    "Z_MOTOR_PV":       "PREFIX:mXX",
    "X_MOTOR_NAME":     "s_x",
    "Y_MOTOR_NAME":     "s_y",
    "Z_MOTOR_NAME":     "s_z",
    "CAMERA_PREFIX":    "CAMERA:cam1:",
    "IMAGE_PREFIX":     "CAMERA:image1:",
    "ROI_PREFIX":       "CAMERA:ROI1:",
    "AUTOFOCUS_STEP":   "0.2"
}
```

---

## File Structure

```
ASWAXS_Sample_View/
├── sample_station.py        # Main application window (PyQt6)
├── sample_view.py           # Earlier single-window version (reference)
├── position_tab.py          # Sample position list + map + interpolation tab
├── position_map_widget.py   # 2D scatter map widget (pyqtgraph)
├── position_models.py       # PositionRecord dataclass, field constants
├── position_io.py           # CSV / JSON / POS load and save functions
├── position_rack_builder.py # Rack / well-plate template builder dialog
├── spline_interpolator.py   # Catmull-Rom spline + arc-length resampling
├── epics_bridge.py          # Thread-safe EPICS CA → Qt bridge
├── frame_decoder.py         # Camera frame decoding (Mono16 → display)
├── autofocus.py             # Autofocus helper
├── autofocus_worker.py      # Off-thread autofocus QObject
├── motor_panel.py           # Motor control panel widget
├── dialogs.py               # Setup and calibration dialogs
├── styles.py                # Qt stylesheet
├── pyproject.toml           # pip packaging metadata
├── _test_smoke.py           # Smoke tests (offscreen, no hardware)
└── Data/                    # Runtime data directory (CSV outputs, etc.)
```

---

## Position File Formats

| Format | Notes |
|---|---|
| `.csv` | Comma-separated with header; flexible column aliases supported |
| `.json` | List of position objects with schema version |
| `.pos` | Whitespace-delimited format compatible with legacy ASWAXS software |

Column aliases for CSV import: `s_x`, `sx`, `sp_x`, `sample_x`, `motor_x`, `vertex_x` are all recognized as `x` (and similarly for `y`, `z`).

---

## Related Repositories

- [FrameByFrame](https://github.com/JIAJTIAN/FrameByFrame) — live SAXS/WAXS data reduction pipeline

---

## Author

Jiajun Tian — ChemMatCARS, University of Chicago  
Contact: jiajtian@uchicago.edu
