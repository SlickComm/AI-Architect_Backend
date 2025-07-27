# cad/surface.py
import ezdxf
from typing import Tuple, Optional

# --------------- Konstanten & Layer ----------------
LAYER_SURF   = "Oberflaeche"      # gestrichelte Linie
LAYER_DIM = "Bemassung_Oberfl"  # (optional) eigenes Layer für Maßkette
DASHED_NAME  = "DASHED"           # wir benutzen denselben Linetype

def register_layers(doc: ezdxf.document.Drawing) -> None:
    """legt Layer + Linetype nur einmalig an"""
    if DASHED_NAME not in doc.linetypes:
        doc.linetypes.new(DASHED_NAME, pattern="A,.5,-.25")

    if LAYER_SURF not in doc.layers:
        doc.layers.new(name=LAYER_SURF,
                       dxfattribs={"color": 0, "linetype": DASHED_NAME})

    if LAYER_DIM not in doc.layers:
        doc.layers.new(name=LAYER_DIM,
                       dxfattribs={"color": 0})        # grün o. Ä.

# --------------- Zeichenfunktion -------------------
def draw_surface_top(msp,
                     trench_top_left: Tuple[float, float],
                     trench_length: float,
                     trench_width: float,
                     offset: float,
                     material_text: Optional[str] = None) -> None:
    """
    Zeichnet die Oberflächen­befestigung (Randzone) in der DRAUFSICHT.

    • trench_top_left  … linke-obere Ecke des Innenrechtecks (Baugraben)
    • trench_length/width … Innenmaße des Grabens
    • offset           … Breite der Randzone rundum
    • material_text    … optionale Beschriftung (z. B. "Gehwegplatten")
    """
    tlx, tly = trench_top_left
    left   = tlx - offset
    right  = tlx + trench_length + offset
    top    = tly - offset
    bottom = tly + trench_width + offset

    # gestrichelter Rand
    msp.add_lwpolyline(
        [(left, top), (right, top),
         (right, bottom), (left, bottom)],
        close=True, dxfattribs={"layer": LAYER_SURF}
    )

    # Beschriftung (mittig über dem Rand)
    if material_text:
        txt_x = tlx + trench_length / 2
        txt_y = top - 0.5
        mtext = msp.add_mtext(material_text,
                              dxfattribs={"layer": LAYER_SURF,
                                          "char_height": 0.3,
                                          "style": "ISOCPEUR"})
        mtext.set_location(insert=(txt_x, txt_y), attachment_point=5)

    # Maßkette (vertikal links)
    msp.add_linear_dim(
        base=(left - 2.0, bottom),      # Linie links vom Rand
        p1=(left, bottom),
        p2=(left, top),
        angle=90,
        override={
            "dimtxt": 0.25,
            "dimclrd": 3,
            "dimexe": 0.2,
            "dimexo": 0.2,
            "dimtad": 1,
        },
        dxfattribs={"layer": LAYER_DIM}
    ).render()
