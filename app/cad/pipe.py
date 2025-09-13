# cad/pipe.py
import ezdxf
from typing import Tuple, Optional

# ---------------------- feste Konstanten ----------------------
LAYER_PIPE = "Rohr"
LAYER_SYM  = "Symmetrie"
LAYER_DIM  = "Bemassung_Rohr"

CLEARANCE_SIDE = 0.50          # 0.5 m Abstand links + rechts

# Dimension-Parameter
DIM_TXT_H   = 0.20                # Texthöhe
DIM_OFFSET  = 0.45                # Abstand Maßlinie → Rohr
DIM_EXE_OFF = 0.10                # Überstand/Versatz der Maßhilfslinien

# ------------------- Layer-Registrierung ----------------------
def register_layers(doc: ezdxf.document.Drawing) -> None:
    if LAYER_PIPE not in doc.layers:
        doc.layers.new(name=LAYER_PIPE, dxfattribs={"color": 0})

    if LAYER_SYM not in doc.layers:
        if "DASHDOT" not in doc.linetypes:
            doc.linetypes.new("DASHDOT", pattern="A,.5,-.25,0,-.25")
        doc.layers.new(name=LAYER_SYM, dxfattribs={"color": 5,
                                                   "linetype": "DASHDOT"})

# --------------------- Zeichenfunktion ------------------------
def draw_pipe_front(
    msp,
    origin_front: Tuple[float, float],
    trench_inner_length: float,
    diameter: float,
    *,
    span_length: Optional[float] = None,
    offset: float = 0.0,
    bottom_y_left: Optional[float] = None,
    bottom_y_right: Optional[float] = None,
) -> float:
    """
    Zeichnet das Rohr in der Vorderansicht und gibt die tatsächlich gezeichnete Länge zurück.
    - Liegen bottom_y_left/right vor, folgt das Rohr dem Gefälle des Innenbodens.
    - Andernfalls liegt das Rohr horizontal bei origin_front[1].
    """
    x_inner_left  = origin_front[0]
    x_inner_right = origin_front[0] + trench_inner_length
    if x_inner_right - x_inner_left <= 1e-9:
        return 0.0

    # Startposition unter Beachtung von Randzone und optionalem Versatz
    x_start = max(x_inner_left + CLEARANCE_SIDE, x_inner_left + max(0.0, float(offset)))
    usable_right = x_inner_right - CLEARANCE_SIDE
    usable = max(0.0, usable_right - x_start)

    want = usable if span_length is None else max(0.0, float(span_length))
    eff_len = min(want, usable)
    if eff_len <= 1e-9:
        return 0.0

    x_end = x_start + eff_len

    # y(x) entlang des Innenbodens (linear), falls angegeben, sonst horizontal
    if bottom_y_left is not None and bottom_y_right is not None:
        def y_on_floor(x: float) -> float:
            t = (x - x_inner_left) / (x_inner_right - x_inner_left)
            return bottom_y_left + t * (bottom_y_right - bottom_y_left)
        y_start_bot = y_on_floor(x_start)
        y_end_bot   = y_on_floor(x_end)
    else:
        y_start_bot = origin_front[1]
        y_end_bot   = origin_front[1]

    # Rohr als Parallelogramm (oben/unten parallel zum Innenboden)
    y_start_top = y_start_bot + float(diameter)
    y_end_top   = y_end_bot   + float(diameter)

    msp.add_lwpolyline(
        [(x_start, y_start_bot),
         (x_end,   y_end_bot),
         (x_end,   y_end_top),
         (x_start, y_start_top)],
        close=True, dxfattribs={"layer": LAYER_PIPE}
    )

    # Symmetrielinie
    msp.add_line(
        (x_start, (y_start_bot + y_start_top) / 2.0),
        (x_end,   (y_end_bot   + y_end_top)   / 2.0),
        dxfattribs={"layer": LAYER_SYM},
    )

    # Maß der gezeichneten Länge (horizontal), Basis unterhalb des tieferen Endes
    dim_y_base = min(y_start_bot, y_end_bot) - DIM_OFFSET
    msp.add_linear_dim(
        base=(x_start, dim_y_base),
        p1=(x_start, y_start_bot),
        p2=(x_end,   y_end_bot),
        angle=0,
        override={
            "dimtxt": DIM_TXT_H,
            "dimclrd": 3,
            "dimexe": DIM_EXE_OFF,
            "dimexo": DIM_EXE_OFF,
            "dimtad": 0,
        },
        dxfattribs={"layer": LAYER_DIM},
    ).render()

    return eff_len
