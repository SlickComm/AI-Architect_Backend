import ezdxf
from typing import Tuple

LAYER_PASS  = "Durchstich"
DEFAULT_PAT = "EARTH"
DEFAULT_COL = 0   
PASS_BOTTOM_GAP = 0.5

DIM_TXT_H   = 0.2   # ⇦ neue Text­höhe (bisher 0.25)
DIM_OFFSET  = 0.5    # ⇦ Abstand Maßkette → Rand (bisher 2.0)
DIM_EXE_OFF = 0.1    # ⇦ Überstand/Versatz der Maßhilfslinien

def register_layers(doc: ezdxf.document.Drawing) -> None:
    if LAYER_PASS not in doc.layers:
        doc.layers.new(name=LAYER_PASS,
                       dxfattribs={"color": DEFAULT_COL})

def draw_pass_front(
    msp,
    *,
    trench_origin: Tuple[float, float],
    trench_len: float,
    trench_depth: float,
    width: float,
    offset: float,
    clearance_left: float,
    clearance_bottom: float,
    pattern: str = DEFAULT_PAT,
) -> None:
    """
    Zeichnet den schraffierten Durchstich in der Vorderansicht.

    offset  = Abstand von der linken Innenkante (nicht Außenkante!) 
              des kombinierten Baugrabens.
    """
    ox, oy = trench_origin

    # horizontale Lage ------------------------------------------------
    inner_left = ox + clearance_left
    x0 = inner_left + offset
    x1 = x0 + width

    # vertikale Lage --------------------------------------------------
    inner_bottom = oy + clearance_bottom
    inner_top    = inner_bottom + trench_depth

    y0 = inner_bottom + PASS_BOTTOM_GAP    # 1 m über Unterkante
    y1 = inner_top                         # oben bündig

    # Umrandung -------------------------------------------------------
    msp.add_lwpolyline(
        [(x0, y0), (x1, y0), (x1, y1), (x0, y1)],
        close=True,
        dxfattribs={"layer": LAYER_PASS},
    )

    # Schraffur -------------------------------------------------------
    hatch = msp.add_hatch(dxfattribs={"layer": LAYER_PASS})
    hatch.set_pattern_fill(pattern, scale=0.10)
    hatch.paths.add_polyline_path(
        [(x0, y0), (x1, y0), (x1, y1), (x0, y1)],
        is_closed=True,
    )

    # --- Bemaßung ---------------------------------------------------
    msp.add_linear_dim(
        base=(x0, y1 + DIM_OFFSET),        # 0.7 m über Oberkante
        p1=(x0, y1),
        p2=(x1, y1),
        angle=0,                    # horizontal
        override={
            "dimtxt": DIM_TXT_H,
            "dimclrd": 3,
            "dimexe": DIM_EXE_OFF,
            "dimexo": DIM_EXE_OFF,
            "dimtad": 0,            # Text über der Linie
        },
        dxfattribs={"layer": LAYER_PASS}
    ).render()