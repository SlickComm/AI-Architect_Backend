# app/cad/surface.py
import ezdxf
from typing import Tuple, Optional, List, Dict

LAYER_SURF   = "Oberflaeche"
LAYER_DIM    = "Bemassung_Oberfl"

DASHED_NAME  = "DASHED"
DASHED_PATTERN   = "A,.18,-.09"

# Dimensions
DIM_TXT_H   = 0.20
DIM_OFFSET  = 0.75
DIM_EXE_OFF = 0.10

SURFACE_LTSCALE = 0.20

def register_layers(doc: ezdxf.document.Drawing) -> None:
    if DASHED_NAME not in doc.linetypes:
        doc.linetypes.new(DASHED_NAME, pattern=DASHED_PATTERN)

    if LAYER_SURF not in doc.layers:
        doc.layers.new(
            name=LAYER_SURF,
            dxfattribs={"color": 0, "linetype": DASHED_NAME}
        )

    if LAYER_DIM not in doc.layers:
        doc.layers.new(name=LAYER_DIM, dxfattribs={"color": 0})

def _dim_override():
    return {
        "dimtxt": DIM_TXT_H,
        "dimclrd": 3,
        "dimexe": DIM_EXE_OFF,
        "dimexo": DIM_EXE_OFF,
        "dimtad": 0,
    }

# ---------- legacy: one uniform offset ----------
# NEU: beide APIs mit Clip-Flags
def draw_surface_top(
    msp,
    trench_top_left: Tuple[float, float],
    trench_length: float,
    trench_width: float,
    offset: float,
    material_text: Optional[str] = None,
    *,                      # <-- nur Keywords
    clip_left: bool = False,
    clip_right: bool = False,
) -> None:
    tlx, tly = trench_top_left
    left   = tlx if clip_left else tlx - offset
    right  = (tlx + trench_length) if clip_right else (tlx + trench_length + offset)
    inner_top  = tly + trench_width
    outer_top  = inner_top + offset
    outer_bot  = tly - offset

    msp.add_lwpolyline(
        [(left, outer_bot), (right, outer_bot), (right, outer_top), (left, outer_top)],
        close=True,
        dxfattribs={"layer": LAYER_SURF, "ltscale": SURFACE_LTSCALE}
    )

    # Vertikalmaß für die Randzone nur am freien linken Ende
    EPS_OFF = 1e-6
    if offset > EPS_OFF and not clip_left:
        msp.add_linear_dim(
            base=(left - DIM_OFFSET, outer_top),
            p1=(left, outer_top), p2=(left, outer_bot),
            angle=90, override=_dim_override(), dxfattribs={"layer": LAYER_DIM}
        ).render()

    # Obere Gesamtlänge genau über der geclippten Outline
    STACK = 0.35
    msp.add_linear_dim(
        base=((left + right)/2.0, outer_top + DIM_OFFSET + STACK),
        p1=(left, outer_top), p2=(right, outer_top), angle=0,
        override=_dim_override(), dxfattribs={"layer": LAYER_DIM}
    ).render()

# ---------- NEW: stepped/segmented offset along the length ----------
def draw_surface_top_segments(
    msp,
    *,
    trench_top_left: Tuple[float, float],
    trench_length: float,
    trench_width: float,
    segments: List[Dict],      # each: {"length": float|None, "offset": float, "material": str?}
    add_dims: bool = True,
    show_total: bool = False,
    clip_left: bool = False,          # <— NEU
    clip_right: bool = False,
) -> None:
    if not segments:
        return

    tlx, tly = trench_top_left
    L = float(trench_length)
    W = float(trench_width)

    # --- normalize segments (lengths & clamp to L) ---
    norm: List[Dict] = []
    total = 0.0
    for i, s in enumerate(segments):
        off = max(0.0, float(s.get("offset", 0.0)))
        seg_len_val = s.get("length", None)
        if seg_len_val is None:
            seg_len = 0.0
        else:
            seg_len = max(0.0, float(seg_len_val))
        norm.append({"length": seg_len, "offset": off, "material": s.get("material", "")})
        total += seg_len

    if total < L:
        # extend last offset to the end
        if norm:
            norm[-1]["length"] += (L - total)
            total = L
    if total > L and norm:
        # clip last segment
        overflow = total - L
        norm[-1]["length"] = max(0.0, norm[-1]["length"] - overflow)

    # --- cumulative boundaries (inner) ---
    boundaries = [0.0]
    acc = 0.0
    for seg in norm:
        acc += seg["length"]
        boundaries.append(acc)  # ends at L

    # --- build outer polygon with steps (clockwise) ---
    # TOP path (left -> right)
    pts = []
    off0 = norm[0]["offset"]
    x_left_outer = tlx if clip_left else (tlx - off0)
    pts = [(x_left_outer, tly - off0)]

    for j in range(len(norm) - 1):
        x_step = tlx + boundaries[j+1]
        off_j  = norm[j]["offset"]
        off_n  = norm[j+1]["offset"]
        pts.append((x_step, tly - off_j))
        pts.append((x_step, tly - off_n))

    off_last = norm[-1]["offset"]
    x_right_outer = (tlx + L) if clip_right else (tlx + L + off_last)
    pts.append((x_right_outer, tly - off_last))            # outer top-right
    pts.append((x_right_outer, tly + W + off_last))

    # Untere Steps
    for j in reversed(range(len(norm) - 1)):
        x_step = tlx + boundaries[j+1]
        off_cur = norm[j+1]["offset"]; off_prev = norm[j]["offset"]
        pts.append((x_step, tly + W + off_cur))
        pts.append((x_step, tly + W + off_prev))

    pts.append((x_left_outer, tly + W + off0))             # left side up
    msp.add_lwpolyline(pts, close=True,
        dxfattribs={"layer": LAYER_SURF, "ltscale": SURFACE_LTSCALE})

    if not add_dims:
        return

    # --- Segment-Längen: an Nahtseiten NICHT verlängern ---
    ov = _dim_override()
    max_off = max(s["offset"] for s in norm)
    y_top_clear = (tly + W + max_off) + DIM_OFFSET + 0.45

    EDGE_EXT_L = 0.0 if clip_left  else 1.0   # vorher 1
    EDGE_EXT_R = 0.0 if clip_right else 1.0

    for j, seg in enumerate(norm):
        x1 = tlx + boundaries[j]
        x2 = tlx + boundaries[j+1]
        if j == 0:
            x1 -= seg["offset"] * EDGE_EXT_L
        if j == len(norm) - 1:
            x2 += seg["offset"] * EDGE_EXT_R
        y_ref = tly + W + seg["offset"]
        msp.add_linear_dim(
            base=((x1 + x2)/2.0, y_top_clear), p1=(x1, y_ref), p2=(x2, y_ref), angle=0,
            override=ov, dxfattribs={"layer": LAYER_DIM}
        ).render()

    # per-segment vertical width dims (showing W + 2*offset)
    EPS_OFF = 1e-6
    for j, seg in enumerate(norm):
        if seg["offset"] <= EPS_OFF:
            continue  # keine Randzone -> keine zusätzliche Vertikalbemaßung

        x1 = tlx + boundaries[j]
        x2 = tlx + boundaries[j+1]
        cx = (x1 + x2)/2.0
        top_y = tly - seg["offset"]
        bot_y = tly + W + seg["offset"]
        msp.add_linear_dim(
            base=(cx, (top_y + bot_y)/2.0),
            p1=(cx, bot_y),
            p2=(cx, top_y),
            angle=90,
            override=ov,
            dxfattribs={"layer": LAYER_DIM},
        ).render()

