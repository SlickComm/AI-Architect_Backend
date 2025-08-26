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
) -> float:
                    
    """
    Zeichnet das Rohr und gibt die *tatsächlich gezeichnete* Länge zurück.
    Es wird nie über die 0,5 m Randzone hinaus gezeichnet.
    """
    # origin_front[0] ist die linke **Innenkante** des Baugrabens
    x_inner_left  = origin_front[0]
    x_inner_right = origin_front[0] + trench_inner_length

    # Start mit Versatz, aber min. 0,5 m Abstand zur linken Innenkante
    x_start = max(x_inner_left + CLEARANCE_SIDE, x_inner_left + max(0.0, float(offset)))

    # Bis max. rechte Innenkante minus 0,5 m
    usable_right = x_inner_right - CLEARANCE_SIDE
    usable = max(0.0, usable_right - x_start)

    want = usable if span_length is None else max(0.0, float(span_length))
    eff_len = min(want, usable)
    if eff_len <= 1e-9:
        return 0.0  # nichts zu zeichnen

    y_bot = origin_front[1]
    y_top = y_bot + float(diameter)

    # Rechteck fürs Rohr:
    msp.add_lwpolyline(
        [(x_start, y_bot), (x_start + eff_len, y_bot),
         (x_start + eff_len, y_top), (x_start, y_top)],
        close=True, dxfattribs={"layer": LAYER_PIPE}
    )

    # Symmetrielinie:
    msp.add_line(
        (x_start, (y_bot + y_top) / 2),
        (x_start + eff_len, (y_bot + y_top) / 2),
        dxfattribs={"layer": LAYER_SYM},
    )

    # Maß der gezeichneten Länge:
    msp.add_linear_dim(
        base=(x_start, y_bot - DIM_OFFSET),
        p1=(x_start, y_bot),
        p2=(x_start + eff_len, y_bot),
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
