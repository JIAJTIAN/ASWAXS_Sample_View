# position_io.py — All position load/save/export functions for CSV, JSON, and POS formats.
# Debug entry point: check _canonical_field output and _split_row parsing for malformed CSVs.
# External deps: none.

import os
import json
import csv
import re

from position_models import (
    _flt,
    PositionRecord,
    POSITION_FIELDS,
    NUMERIC_FIELDS,
    DEFAULT_ROLE,
    DEFAULT_LAYOUT,
    CSV_ALIASES,
)


def normalize_positions(rows: list) -> list:
    return [r.to_dict() if isinstance(r, PositionRecord)
            else PositionRecord.from_mapping(r, index=i).to_dict()
            for i, r in enumerate(rows)]


def blank_position(index: int = 0, *, layout: str = DEFAULT_LAYOUT) -> dict:
    return PositionRecord(name=f"pos_{index+1}", layout=layout).to_dict()


def _canonical_field(v: str) -> str:
    k = v.strip().lstrip("#").lower().replace(" ", "_").replace("-", "_")
    return CSV_ALIASES.get(k, k)


def _split_row(v: str) -> list:
    if "," in v:
        rows = list(csv.reader([v]))[0]
        return [x.strip() for x in rows]
    return [x for x in re.split(r'[\t,; ]+', v.strip()) if x]


def _layout_from_header(header: list) -> str:
    cf = [_canonical_field(h) for h in header]
    has_xyz    = any(f in cf for f in ("x", "y", "z"))
    has_normal = any(f.startswith("normal") for f in cf)
    if has_xyz and has_normal:
        return "blender_interpolated"
    return "freeform"


def _role_from_header(header: list) -> str:
    cf = [_canonical_field(h) for h in header]
    if any(f.startswith("normal") for f in cf):
        return "Interpolated"
    return "Sample"


def _load_csv(path) -> list:
    result = []
    header = None
    default_layout = "freeform"
    default_role   = "Sample"
    with open(path, newline='', encoding='utf-8', errors='replace') as f:
        for line in f:
            stripped = line.rstrip('\n').strip()
            if not stripped:
                continue
            if stripped.startswith('#') and any(c.isalpha() for c in stripped[1:]):
                raw_parts = _split_row(stripped.lstrip('#').strip())
                header = [_canonical_field(h) for h in raw_parts]
                default_layout = _layout_from_header(raw_parts)
                default_role   = _role_from_header(raw_parts)
                continue
            if stripped.startswith('#'):
                continue
            parts = _split_row(stripped)
            if not parts:
                continue
            # detect a header row written without '#' (e.g. by _save_csv / DictWriter)
            if header is None:
                canonical = [_canonical_field(p) for p in parts]
                if "x" in canonical and "y" in canonical:
                    header = canonical
                    default_layout = _layout_from_header(parts)
                    default_role   = _role_from_header(parts)
                    continue
            if header:
                row = {header[i]: parts[i] for i in range(min(len(header), len(parts)))}
            else:
                row = {}
                for i, f in enumerate(["x", "y", "z"]):
                    if i < len(parts):
                        row[f] = parts[i]
            row.setdefault("layout", default_layout)
            row.setdefault("role",   default_role)
            result.append(row)
    return normalize_positions(result)


def _save_csv(path, positions) -> None:
    positions = normalize_positions(positions)
    with open(path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=POSITION_FIELDS)
        writer.writeheader()
        for pos in positions:
            writer.writerow(pos)


def _load_json(path) -> list:
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    if isinstance(data, dict):
        rows = data.get("positions", [])
    else:
        rows = data
    return normalize_positions(rows)


def _save_json(path, positions) -> None:
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(
            {"schema": "aswaxs-sample-position-v1",
             "positions": normalize_positions(positions)},
            f, indent=2,
        )


def _load_pos(path) -> list:
    result = []
    header = None
    with open(path, encoding='utf-8', errors='replace') as f:
        for line in f:
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith('#'):
                header = stripped.lstrip('#').split()
                continue
            parts = (stripped.split() if ',' not in stripped
                     else [x.strip() for x in stripped.split(',')])
            if not parts:
                continue
            row: dict = {}
            if header and len(header) == len(parts):
                row = {_canonical_field(header[i]): parts[i] for i in range(len(parts))}
            else:
                for i, f in enumerate(["x", "y", "z"]):
                    if i < len(parts):
                        row[f] = parts[i]
            row.setdefault("layout", "pos")
            result.append(row)
    return normalize_positions(result)


def _save_pos(path, positions) -> None:
    positions = normalize_positions(positions)
    with open(path, 'w', encoding='utf-8') as f:
        f.write("# x y z\n")
        for pos in positions:
            f.write(f"{float(pos['x']):.6f} {float(pos['y']):.6f} {float(pos['z']):.6f}\n")


def load_positions(path) -> list:
    path = str(path)
    suffix = os.path.splitext(path)[1].lower()
    if suffix == '.json':
        return _load_json(path)
    elif suffix == '.pos':
        return _load_pos(path)
    else:
        return _load_csv(path)


def save_positions(path, positions: list) -> None:
    path = str(path)
    suffix = os.path.splitext(path)[1].lower()
    if suffix == '.json':
        _save_json(path, positions)
    elif suffix == '.pos':
        _save_pos(path, positions)
    else:
        _save_csv(path, positions)


def export_bluesky_csv(path, positions: list) -> None:
    positions = normalize_positions(positions)
    fieldnames = ["s_x", "s_y", "s_z", "name", "role", "layout",
                  "group", "solvent_group", "note"]
    with open(path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for pos in positions:
            writer.writerow({
                "s_x": pos["x"], "s_y": pos["y"], "s_z": pos["z"],
                "name": pos["name"], "role": pos["role"], "layout": pos["layout"],
                "group": pos["group"], "solvent_group": pos["solvent_group"],
                "note": pos["note"],
            })


def export_reducer_pairs_csv(path, positions: list) -> None:
    positions = normalize_positions(positions)
    fieldnames = ["name", "sample_group", "solvent_group",
                  "sample_x", "sample_y", "sample_z", "role"]
    with open(path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for pos in positions:
            if str(pos.get("role", "")).strip() == "Sample":
                writer.writerow({
                    "name":          pos["name"],
                    "sample_group":  pos.get("group", ""),
                    "solvent_group": pos["solvent_group"],
                    "sample_x":      pos["x"],
                    "sample_y":      pos["y"],
                    "sample_z":      pos["z"],
                    "role":          pos["role"],
                })
