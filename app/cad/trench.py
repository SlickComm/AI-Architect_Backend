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
    hatch.set_pattern_fill(HATCH_PATTERN, scale=HATCH_SCALE)
    hatch.paths.add_polyline_path(outer, is_closed=True)
    hatch.paths.add_polyline_path(inner, is_closed=True)


def draw_trench_top(
    msp,
    top_left: Tuple[float, float],
    length: float,
    width:  float,
) -> None:
    """Draufsicht (Rechteck + Maße)."""
    x, y = top_left
    rect = [(x, y),
            (x + length, y),
            (x + length, y + width),
            (x, y + width)]
    msp.add_lwpolyline(rect, close=True,
                       dxfattribs={"layer": LAYER_TRENCH_OUT})

    dim = msp.add_linear_dim
    dim(
        base=(x, y - DIM_OFFSET),
        p1=(x, y), p2=(x + length, y), angle=0,
        override=_dim_style(), dxfattribs={"layer": LAYER_TRENCH_OUT}
    ).render()
    dim(
        base=(x - DIM_OFFSET, y),
        p1=(x, y), p2=(x, y + width), angle=90,
        override=_dim_style(), dxfattribs={"layer": LAYER_TRENCH_OUT}
    ).render()

# ---------- Hilfs-Style ----------
def _dim_style():
    return {
        "dimtxt": DIM_TXT_H,
        "dimclrd": 3,
        "dimexe": DIM_EXE_OFF,
        "dimexo": DIM_EXE_OFF,
        "dimtad": 0,
    }
