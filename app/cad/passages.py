import ezdxf
from ezdxf.enums import const
from typing import Tuple, Optional

LAYER_PASS  = "Durchstich"
DEFAULT_PAT = "EARTH"
DEFAULT_COL = 0
PASS_BOTTOM_GAP = 0.5

DIM_TXT_H   = 0.2
DIM_OFFSET  = 0.5
DIM_EXE_OFF = 0.1

def register_layers(doc: ezdxf.document.Drawing) -> None:
    if LAYER_PASS not in doc.layers:
        doc.layers.new(name=LAYER_PASS, dxfattribs={"color": DEFAULT_COL})

def draw_pass_front(
    msp, *,
    trench_origin: Tuple[float, float],
    trench_len: float,
    trench_depth: float,
    width: float,
    offset: float,
    clearance_left: float,
    clearance_bottom: float,
    pattern: str = DEFAULT_PAT,
    hatch_scale: float = 0.05,        
    seed_point: Optional[Tuple[float, float]] = (0.0, 0.0),
) -> None:
    ox, oy = trench_origin

    # Lage -------------------------------------------------------------
    inner_left = ox + clearance_left
    x0 = inner_left + offset
    x1 = x0 + width

    inner_bottom = oy + clearance_bottom
    inner_top    = inner_bottom + trench_depth
    y0 = inner_bottom + PASS_BOTTOM_GAP
    y1 = inner_top

    # --- HATCH zuerst, leicht eingerückt ------------------------------
    EPS = 1e-3
    hx0, hx1 = x0 + EPS, x1 - EPS
    hy0, hy1 = y0 + EPS, y1 - EPS

    hatch = msp.add_hatch(dxfattribs={"layer": LAYER_PASS})
    hatch.dxf.associative = 0

    # nur für EARTH drehen
    angle = 45.0 if (pattern or "").upper() == "EARTH" else 0.0
    hatch.set_pattern_fill(pattern, scale=hatch_scale, angle=angle)

    hatch.paths.add_polyline_path(
        [(hx0, hy0), (hx1, hy0), (hx1, hy1), (hx0, hy1)],
        is_closed=True,
        flags=const.BOUNDARY_PATH_OUTERMOST,
    )

    # globaler Seed -> keine Kachelnaht mitten im Durchstich
    if seed_point is not None and hasattr(hatch, "set_seed_points"):
        hatch.set_seed_points([seed_point])

    # --- Rahmen NACH dem Hatch zeichnen (liegt oben) ------------------
    msp.add_lwpolyline(
        [(x0, y0), (x1, y0), (x1, y1), (x0, y1)],
        close=True,
        dxfattribs={"layer": LAYER_PASS},
    )

    # --- Bemaßung -----------------------------------------------------
    msp.add_linear_dim(
        base=(x0, y1 + DIM_OFFSET),
        p1=(x0, y1),
        p2=(x1, y1),
        angle=0,
        override={
            "dimtxt": DIM_TXT_H,
            "dimclrd": 3,
            "dimexe": DIM_EXE_OFF,
            "dimexo": DIM_EXE_OFF,
            "dimtad": 0,
        },
        dxfattribs={"layer": LAYER_PASS}
    ).render()
