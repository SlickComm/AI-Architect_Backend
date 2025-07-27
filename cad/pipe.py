# cad/pipe.py
import ezdxf
from typing import Tuple

# ---------------------- feste Konstanten ----------------------
LAYER_PIPE = "Rohr"
LAYER_SYM  = "Symmetrie"

CLEARANCE_SIDE = 0.50          # 0.5 m Abstand links + rechts

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
def draw_pipe_front(msp,
                    origin_front: Tuple[float, float],
                    trench_inner_length: float,
                    diameter: float) -> None:
                    
    left  = origin_front[0] + CLEARANCE_SIDE
    right = origin_front[0] + trench_inner_length - CLEARANCE_SIDE
    y_bot = origin_front[1]
    y_top = y_bot + diameter

    # Rohrquerschnitt (rechteckig)
    msp.add_lwpolyline(
        [(left, y_bot), (right, y_bot),
         (right, y_top), (left, y_top)],
        close=True, dxfattribs={"layer": LAYER_PIPE}
    )

    # Symmetrielinie
    msp.add_line(
        (left, (y_bot + y_top) / 2),
        (right, (y_bot + y_top) / 2),
        dxfattribs={"layer": LAYER_SYM}
    )
