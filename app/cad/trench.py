import ezdxf
from typing import Tuple

# ---------- Konstanten & Layer ----------
LAYER_TRENCH_OUT = "Baugraben"
LAYER_TRENCH_IN  = "InnerRechteck"
LAYER_HATCH      = "Zwischenraum"

HATCH_PATTERN = "EARTH"
HATCH_SCALE   = 0.05

DIM_TXT_H   = 0.2   # ⇦ neue Text­höhe (bisher 0.25)
DIM_OFFSET  = 0.5    # ⇦ Abstand Maßkette → Rand (bisher 2.0)
DIM_OFFSET_FRONT  = 0.7
DIM_EXE_OFF = 0.1    # ⇦ Überstand/Versatz der Maßhilfslinien
TOP_DIM_EXTRA = 0.55   # Zusatzabstand für die obere Längenmaßkette (z. B. "500")

# ---------- Layer-Registrierung ----------
def register_layers(doc: ezdxf.document.Drawing) -> None:
    if LAYER_TRENCH_OUT not in doc.layers:
        doc.layers.new(name=LAYER_TRENCH_OUT, dxfattribs={"color": 0})
    if LAYER_TRENCH_IN not in doc.layers:
        doc.layers.new(name=LAYER_TRENCH_IN,  dxfattribs={"color": 0})
    if LAYER_HATCH not in doc.layers:
        doc.layers.new(name=LAYER_HATCH,      dxfattribs={"color": 0})

# ---------- Zeichenfunktionen ----------
def draw_trench_front(
    msp,
    origin: Tuple[float, float],
    length: float,
    depth:  float,
    clearance_left: float = 0.2,
    clearance_bottom: float = 0.2,
) -> None:
    """Äußeres + inneres Rechteck (Front), Bemaßung, Schraffur."""
    ox, oy = origin

    # äußeres Rechteck
    outer = [
        (ox, oy),
        (ox + length + 2 * clearance_left, oy),
        (ox + length + 2 * clearance_left, oy + depth + clearance_bottom),
        (ox, oy + depth + clearance_bottom),
    ]
    msp.add_lwpolyline(outer, close=True,
                       dxfattribs={"layer": LAYER_TRENCH_OUT})

    # inneres Rechteck
    inner = [
        (ox + clearance_left,                   oy + clearance_bottom),
        (ox + clearance_left + length,          oy + clearance_bottom),
        (ox + clearance_left + length,          oy + clearance_bottom + depth),
        (ox + clearance_left,                   oy + clearance_bottom + depth),
    ]
    msp.add_lwpolyline(inner, close=True,
                       dxfattribs={"layer": LAYER_TRENCH_IN})

    # Bemaßung
    dim = msp.add_linear_dim
    dim(
        base=(inner[0][0], inner[0][1] - DIM_OFFSET_FRONT),
        p1=inner[0], p2=inner[1], angle=0,
        override=_dim_style(), dxfattribs={"layer": LAYER_TRENCH_OUT}
    ).render()
    dim(
        base=(inner[1][0] + DIM_OFFSET_FRONT, inner[1][1]),
        p1=inner[2], p2=inner[1], angle=90,
        override=_dim_style(), dxfattribs={"layer": LAYER_TRENCH_OUT}
    ).render()

    # Schraffur
    hatch = msp.add_hatch(color=4, dxfattribs={"layer": LAYER_HATCH})
    hatch.set_pattern_fill(HATCH_PATTERN, scale=HATCH_SCALE,
                        angle=45.0 if HATCH_PATTERN.upper() == "EARTH" else 0.0)
    hatch.paths.add_polyline_path(outer, is_closed=True)
    hatch.paths.add_polyline_path(inner, is_closed=True)

def draw_trench_top(msp, top_left, length, width):
    x, y = top_left
    rect = [(x, y), (x+length, y), (x+length, y+width), (x, y+width)]
    msp.add_lwpolyline(rect, close=True, dxfattribs={"layer": LAYER_TRENCH_OUT})

    y_top = y + width  # obere Innenkante
    msp.add_linear_dim(
        base=(x, y_top + DIM_OFFSET + TOP_DIM_EXTRA),              # Maßlinie ÜBER der oberen Kante
        p1=(x, y_top), p2=(x + length, y_top),     # entlang der oberen Kante messen
        angle=0,
        override=_dim_style(), dxfattribs={"layer": LAYER_TRENCH_OUT}
    ).render()

    msp.add_linear_dim(
        base=(x - DIM_OFFSET, y),                  # wie gehabt (links)
        p1=(x, y), p2=(x, y + width), angle=90,
        override=_dim_style(), dxfattribs={"layer": LAYER_TRENCH_OUT}
    ).render()

def draw_trench_front_lr(
    msp, origin: Tuple[float, float], length: float, depth: float, *,
    clear_left: float = 0.2, 
    clear_right: float = 0.2, 
    clear_bottom: float = 0.2,
    bottom_clip_left: float = 0.0, 
    top_clip_left: float = 0.0,
    top_len_from_left: float | None = None, 
    vertical_clip_right: float = 0.0,
    draw_left_inner: bool = True,
    draw_right_inner: bool = True, 
    draw_outer: bool = True,
    gap_top_from_left: float | None = None,    # NEW: Lücke in der Decke
    gap_top_len:       float | None = None,    # NEW: Breite der Lücke
    gap_bot_from_left: float | None = None,    # optional: Lücke im Boden
    gap_bot_len:       float | None = None,    # optional
):
    ox, oy = origin
    y_bot = oy + clear_bottom
    y_top = y_bot + depth

    def _hline(x1, x2, y):
        if x2 - x1 > 1e-9:
            msp.add_lwpolyline([(x1, y), (x2, y)], dxfattribs={"layer": LAYER_TRENCH_IN})

    if draw_outer:
        outer = [
            (ox, oy),
            (ox + clear_left + length + clear_right, oy),
            (ox + clear_left + length + clear_right, oy + depth + clear_bottom),
            (ox, oy + depth + clear_bottom),
        ]
        msp.add_lwpolyline(outer, close=True, dxfattribs={"layer": LAYER_TRENCH_OUT})

    if clear_left > 0 or clear_right > 0:
        x0    = ox + clear_left
        x_rgt = x0 + length
        x_lft = x0 + bottom_clip_left

        # ── Boden (mit optionaler Lücke)
        if gap_bot_from_left is None or gap_bot_len is None:
            _hline(x_lft, x_rgt, y_bot)
        else:
            g0 = x0 + max(0.0, gap_bot_from_left)
            g1 = x0 + min(length, gap_bot_from_left + gap_bot_len)
            _hline(x_lft, min(x_rgt, g0), y_bot)
            _hline(max(x_lft, g1), x_rgt, y_bot)
            
        # linke Innenwand
        if draw_left_inner:
            msp.add_lwpolyline([(x0, y_bot), (x0, y_top)], dxfattribs={"layer": LAYER_TRENCH_IN})

        # rechte Innenwand – nur falls gewünscht
        if draw_right_inner:
            y_stop = y_bot if vertical_clip_right <= 0 else max(y_bot, y_top - vertical_clip_right)
            msp.add_lwpolyline([(x_rgt, y_stop), (x_rgt, y_top)], dxfattribs={"layer": LAYER_TRENCH_IN})

        # ── Decke (mit optionaler Lücke)
        x_start = x0 + top_clip_left
        x_end   = x_rgt if top_len_from_left is None else min(x_rgt, x0 + top_len_from_left)
        if x_end > x_start:
            if gap_top_from_left is None or gap_top_len is None:
                _hline(x_start, x_end, y_top)
            else:
                g0 = x0 + max(0.0, gap_top_from_left)
                g1 = x0 + min(length, gap_top_from_left + gap_top_len)
                _hline(x_start, min(x_end, g0), y_top)
                _hline(max(x_start, g1), x_end, y_top)

# ---------- Hilfs-Style ----------
def _dim_style():
    return {
        "dimtxt": DIM_TXT_H,
        "dimclrd": 3,
        "dimexe": DIM_EXE_OFF,
        "dimexo": DIM_EXE_OFF,
        "dimtad": 0,
    }