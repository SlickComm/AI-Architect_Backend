from __future__ import annotations

import ezdxf
from typing import Tuple, Optional

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
    *,
    depth_left: Optional[float] = None,
    depth_right: Optional[float] = None,
) -> None:
    ox, oy = origin
    dL = depth if depth_left  is None else float(depth_left)
    dR = depth if depth_right is None else float(depth_right)
    dMAX = max(dL, dR, depth)

    # inneres Trapez (mit Gefälle)
    x0 = ox + clearance_left
    yb = oy + clearance_bottom
    x1 = x0 + length
    y_top = yb + dMAX              # Decke = waagrecht

    yBL = y_top - dL           # Innenboden links
    yBR = y_top - dR           # Innenboden rechts
    yOBL = yBL - clearance_bottom   # Außenboden links
    yOBR = yBR - clearance_bottom   # Außenboden rechts

    EPS = 1e-6  # 1 µm reicht; nur top-Kante entkoppeln
    same_depth = abs(dL - dR) < EPS  

    inner = [(x0, yBL), (x1, yBR), (x1, y_top - EPS), (x0, y_top - EPS)]
    msp.add_lwpolyline(inner, close=True, dxfattribs={"layer": LAYER_TRENCH_IN})

    outer = [
        (ox,                             yOBL),
        (ox + length + 2*clearance_left, yOBR),
        (ox + length + 2*clearance_left, y_top),
        (ox,                             y_top),
    ]
    msp.add_lwpolyline(outer, close=True, dxfattribs={"layer": LAYER_TRENCH_OUT})

    # Längenmaß (unten)
    # msp.add_linear_dim(
    #     base=(x0, yb - DIM_OFFSET_FRONT),
    #     p1=(x0, yb), p2=(x1, yb), angle=0,
    #     override=_dim_style(), dxfattribs={"layer": LAYER_TRENCH_OUT}
    # ).render()

    # Längenmaß bleibt, Tiefenmaße von Decke nach unten:
    msp.add_linear_dim(
        base=(x0 - DIM_OFFSET_FRONT, yb),
        p1=(x0, y_top), p2=(x0, yBL), angle=90,
        override=_dim_style(), dxfattribs={"layer": LAYER_TRENCH_OUT}
    ).render()

    # rechts NUR wenn die Tiefe dort anders ist
    if not same_depth:
        msp.add_linear_dim(
            base=(x1 + DIM_OFFSET_FRONT, yb),
            p1=(x1, y_top), p2=(x1, yBR), angle=90,
            override=_dim_style(), dxfattribs={"layer": LAYER_TRENCH_OUT}
        ).render()

    # Schraffur = outer - inner
    hatch = msp.add_hatch(color=4, dxfattribs={"layer": LAYER_HATCH})
    hatch.set_pattern_fill(HATCH_PATTERN, scale=HATCH_SCALE,
                           angle=45.0 if HATCH_PATTERN.upper() == "EARTH" else 0.0)
    hatch.paths.add_polyline_path(outer, is_closed=True) 
    hatch.paths.add_polyline_path(inner, is_closed=True) 

def draw_trench_top(msp, top_left, length, width, *, clip_left=False, clip_right=False, dim_right=False, ):
    x, y = top_left

    # Außenkanten (offen zeichnen, damit wir Einzelkanten weglassen können)
    msp.add_lwpolyline([(x, y), (x + length, y)], dxfattribs={"layer": LAYER_TRENCH_OUT})
    msp.add_lwpolyline([(x, y + width), (x + length, y + width)], dxfattribs={"layer": LAYER_TRENCH_OUT})
    if not clip_left:
        msp.add_lwpolyline([(x, y), (x, y + width)], dxfattribs={"layer": LAYER_TRENCH_OUT})
    if not clip_right:
        msp.add_lwpolyline([(x + length, y), (x + length, y + width)], dxfattribs={"layer": LAYER_TRENCH_OUT})

    # Bemaßungen wie gehabt
    y_top = y + width
    msp.add_linear_dim(
        base=(x, y_top + DIM_OFFSET + TOP_DIM_EXTRA),
        p1=(x, y_top), p2=(x + length, y_top), angle=0,
        override=_dim_style(), dxfattribs={"layer": LAYER_TRENCH_OUT}
    ).render()

    # ← links standard | → rechts, wenn dim_right=True
    vx = (x + length) if dim_right else x
    base_x = (vx + DIM_OFFSET) if dim_right else (x - DIM_OFFSET)
    msp.add_linear_dim(
        base=(base_x, y),
        p1=(vx, y), p2=(vx, y + width), angle=90,
        override=_dim_style(), dxfattribs={"layer": LAYER_TRENCH_OUT}
    ).render()

def draw_trench_front_lr(
    msp, origin: Tuple[float, float], length: float, depth: float, *,
    clear_left: float = 0.2, clear_right: float = 0.2, clear_bottom: float = 0.2,
    bottom_clip_left: float = 0.0, top_clip_left: float = 0.0,
    top_len_from_left: Optional[float] = None, vertical_clip_right: float = 0.0,
    draw_left_inner: bool = True, draw_right_inner: bool = True, draw_outer: bool = True,
    gap_top_from_left: Optional[float] = None, gap_top_len: Optional[float] = None,
    gap_bot_from_left: Optional[float] = None, gap_bot_len: Optional[float] = None,
    depth_left: Optional[float] = None, depth_right: Optional[float] = None,
    draw_bottom=True
):
    ox, oy = origin
    yb   = oy + clear_bottom
    dL   = depth if depth_left  is None else float(depth_left)
    dR   = depth if depth_right is None else float(depth_right)

    x0 = ox + clear_left
    xR = x0 + length

    # Decke oben: fest
    y_top = yb + max(depth, dL, dR)
    # Boden links/rechts: schräg
    yBL   = y_top - dL
    yBR   = y_top - dR

    def _hline(x1, x2, y):  # Boden (wie gehabt)
        if x2 - x1 > 1e-9:
            msp.add_lwpolyline([(x1, y), (x2, y)], dxfattribs={"layer": LAYER_TRENCH_IN})

    def _sline(xa, ya, xb, yb_):
        if xb - xa > 1e-9:
            msp.add_lwpolyline([(xa, ya), (xb, yb_)], dxfattribs={"layer": LAYER_TRENCH_IN})

    def y_at(x):
        # Boden-Schräge zwischen (x0,yBL) und (xR,yBR)
        if length <= 1e-9: return yBL
        t = (x - x0) / length
        return yBL + t * (yBR - yBL)

    if draw_outer:
        y_top = yb + max(depth, dL, dR)
        yBL   = y_top - dL
        yBR   = y_top - dR
        yOBL  = yBL - clear_bottom
        yOBR  = yBR - clear_bottom
        outer = [
            (ox,                               yOBL),
            (ox + clear_left + length + clear_right, yOBR),
            (ox + clear_left + length + clear_right, y_top),
            (ox,                               y_top),
        ]
        msp.add_lwpolyline(outer, close=True, dxfattribs={"layer": LAYER_TRENCH_OUT})

    if clear_left > 0 or clear_right > 0:
        x0    = ox + clear_left
        x_rgt = x0 + length
        x_lft = x0 + bottom_clip_left

        # ── Boden (mit optionaler Lücke)
        # if gap_bot_from_left is None or gap_bot_len is None:
        #     _hline(x_lft, x_rgt, yb)
        # else:
        #     g0 = x0 + max(0.0, gap_bot_from_left)
        #     g1 = x0 + min(length, gap_bot_from_left + gap_bot_len)
        #     _hline(x_lft, min(x_rgt, g0), yb)
        #     _hline(max(x_lft, g1), x_rgt, yb)
            
        # Vertikale Innenlinien
        if draw_left_inner:
            msp.add_lwpolyline([(x0, yBL), (x0, y_top)], dxfattribs={"layer": LAYER_TRENCH_IN})
        if draw_right_inner:
            y_start = yBR
            if vertical_clip_right > 0:
                y_start = max(yb, y_start)
            msp.add_lwpolyline([(xR, y_start), (xR, y_top)], dxfattribs={"layer": LAYER_TRENCH_IN})

        # Decke (waagrecht) – ggf. mit Lücke oben:
        x_start_top = x0 + top_clip_left
        x_end_top   = xR if top_len_from_left is None else min(xR, x0 + top_len_from_left)
        
        def _hline(x1, x2, y):
            if x2 - x1 > 1e-9:
                msp.add_lwpolyline([(x1, y), (x2, y)], dxfattribs={"layer": LAYER_TRENCH_IN})

        if gap_top_from_left is None or gap_top_len is None:
            _hline(x_start_top, x_end_top, y_top)
        else:
            g0 = x0 + max(0.0, gap_top_from_left)
            g1 = x0 + min(length, gap_top_from_left + gap_top_len)
            _hline(x_start_top, min(x_end_top, g0), y_top)
            _hline(max(x_start_top, g1), x_end_top, y_top)

        # # Boden (schräg) – ohne Lücke, oder optional mit gap_bot_* wenn gebraucht:
        # x_start_bot = x0  # ggf. analog zu top_clip_left umbenutzen/verwenden
        # x_end_bot   = xR
        # if gap_bot_from_left is None or gap_bot_len is None:
        #     msp.add_lwpolyline([(x_start_bot, y_at(x_start_bot)), (x_end_bot, y_at(x_end_bot))],
        #                     dxfattribs={"layer": LAYER_TRENCH_IN})
        # else:
        #     gb0 = x0 + max(0.0, gap_bot_from_left)
        #     gb1 = x0 + min(length, gap_bot_from_left + gap_bot_len)
        #     msp.add_lwpolyline([(x_start_bot, y_at(x_start_bot)), (min(x_end_bot, gb0), y_at(min(x_end_bot, gb0)))],
        #                     dxfattribs={"layer": LAYER_TRENCH_IN})
        #     msp.add_lwpolyline([(max(x_start_bot, gb1), y_at(max(x_start_bot, gb1))), (x_end_bot, y_at(x_end_bot))],
        #                     dxfattribs={"layer": LAYER_TRENCH_IN})

        # --- BODEN: nur wenn draw_bottom=True ---
        if draw_bottom:
            # unten (horizontal) – optional mit Lücke
            if gap_bot_from_left is None or gap_bot_len is None:
                _hline(x_lft, x_rgt, yb)
            else:
                g0 = x0 + max(0.0, gap_bot_from_left)
                g1 = x0 + min(length, gap_bot_from_left + gap_bot_len)
                _hline(x_lft, min(x_rgt, g0), yb)
                _hline(max(x_lft, g1), x_rgt, yb)

            # unten (schräg/„Bodenverlauf“)
            x_start_bot = x0
            x_end_bot   = x_rgt
            if gap_bot_from_left is None or gap_bot_len is None:
                msp.add_lwpolyline([(x_start_bot, y_at(x_start_bot)), (x_end_bot, y_at(x_end_bot))],
                                   dxfattribs={"layer": LAYER_TRENCH_IN})
            else:
                gb0 = x0 + max(0.0, gap_bot_from_left)
                gb1 = x0 + min(length, gap_bot_from_left + gap_bot_len)
                msp.add_lwpolyline([(x_start_bot, y_at(x_start_bot)), (min(x_end_bot, gb0), y_at(min(x_end_bot, gb0)))],
                                   dxfattribs={"layer": LAYER_TRENCH_IN})
                msp.add_lwpolyline([(max(x_start_bot, gb1), y_at(max(x_start_bot, gb1))), (x_end_bot, y_at(x_end_bot))],
                                   dxfattribs={"layer": LAYER_TRENCH_IN})

# ---------- Hilfs-Style ----------
def _dim_style():
    return {
        "dimtxt": DIM_TXT_H,
        "dimclrd": 3,
        "dimexe": DIM_EXE_OFF,
        "dimexo": DIM_EXE_OFF,
        "dimtad": 0,
    }