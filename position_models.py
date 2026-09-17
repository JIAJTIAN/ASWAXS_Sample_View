# position_models.py — PositionRecord dataclass and all shared position constants.
# Debug entry point: check PositionRecord.from_mapping if a position dict has wrong types.
# External deps: none.

from dataclasses import asdict, dataclass
from typing import Any


# ── Default application configuration ─────────────────────────────────────────

DEFAULT_CONFIG = {
    "X_MOTOR_PV":       "15IDD:m19",
    "Y_MOTOR_PV":       "15IDD:m18",
    "Z_MOTOR_PV":       "15IDD:m7",
    "X_MOTOR_NAME":     "s_x",
    "Y_MOTOR_NAME":     "s_y",
    "Z_MOTOR_NAME":     "s_z",
    "CAMERA_PREFIX":    "Teslong:cam1:",
    "IMAGE_PREFIX":     "Teslong:image1:",
    "ROI_PREFIX":       "15PS1:ROI1:",
    "AUTOFOCUS_STEP":   "0.2",
}

# ── Position field definitions ─────────────────────────────────────────────────

POSITION_FIELDS = ["name", "x", "y", "z", "role", "layout", "group", "solvent_group", "note"]
NUMERIC_FIELDS  = {"x", "y", "z"}
DEFAULT_ROLE    = "Sample"
DEFAULT_LAYOUT  = "freeform"
ROLE_PRESETS    = ["Sample", "Solvent", "GC", "Air", "Empty", "Standard",
                   "Background", "Inlet", "Outlet", "Channel", "Observation", "Skip"]
ROLE_COLORS = {
    "Sample":       "#4C78A8",
    "Solvent":      "#54A24B",
    "Empty":        "#BAB0AC",
    "Standard":     "#F58518",
    "Air":          "#E45756",
    "GC":           "#B279A2",
    "Background":   "#72B7B2",
    "Inlet":        "#1F77B4",
    "Outlet":       "#D62728",
    "Channel":      "#59A14F",
    "Observation":  "#EDC948",
    "Skip":         "#D8DCE3",
    "Interpolated": "#A855F7",
}

# ── CSV column aliases ─────────────────────────────────────────────────────────

CSV_ALIASES = {
    "s_x": "x", "sx": "x", "sp_x": "x", "sample_x": "x", "motor_x": "x",
    "vertex_x": "x", "vertexx": "x",
    "s_y": "y", "sy": "y", "sp_y": "y", "sample_y": "y", "motor_y": "y",
    "vertex_y": "y", "vertexy": "y",
    "s_z": "z", "sz": "z", "sp_z": "z", "sample_z": "z", "motor_z": "z",
    "vertex_z": "z", "vertexz": "z",
    "comment": "note", "notes": "note",
    "solvent": "solvent_group", "solventgroup": "solvent_group",
    "solvent_group_index": "solvent_group",
}

# ── Float coercion helper (needed by PositionRecord.from_mapping) ──────────────

def _flt(v: Any) -> float:
    if v is None or v == "":
        return 0.0
    try:
        return float(v)
    except Exception:
        return 0.0


# ── Position data model ────────────────────────────────────────────────────────

@dataclass
class PositionRecord:
    name: str = ""
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    role: str = DEFAULT_ROLE
    layout: str = DEFAULT_LAYOUT
    group: str = ""
    solvent_group: str = ""
    note: str = ""

    @classmethod
    def from_mapping(cls, data: dict, index: int | None = None) -> "PositionRecord":
        normalized = {f: data.get(f, "") for f in POSITION_FIELDS}
        for f in NUMERIC_FIELDS:
            normalized[f] = _flt(normalized.get(f))
        if not str(normalized["name"]).strip():
            normalized["name"] = f"pos_{index+1}" if index is not None else "pos"
        for f in ("role", "layout"):
            if not str(normalized[f]).strip():
                normalized[f] = DEFAULT_ROLE if f == "role" else DEFAULT_LAYOUT
        return cls(
            name=str(normalized["name"]).strip(),
            x=float(normalized["x"]), y=float(normalized["y"]), z=float(normalized["z"]),
            role=str(normalized["role"]).strip(), layout=str(normalized["layout"]).strip(),
            group=str(normalized["group"]).strip(),
            solvent_group=str(normalized["solvent_group"]).strip(),
            note=str(normalized["note"]).strip(),
        )

    def to_dict(self) -> dict:
        return asdict(self)
