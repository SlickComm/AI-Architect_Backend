from fastapi import FastAPI, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from openai import OpenAI

import ezdxf
from ezdxf.enums import const

import os
import uuid
import json

app = FastAPI()

# OpenAI-Key
client = OpenAI(api_key="")

OPENAI_PROMPT = """
You are an expert system that converts German construction LV texts into a JSON format with the following schema:
{
  "elements": [
    {
      "type":"string",
      "length":0.0,
      "width":0.0,
      "depth":0.0,
      "diameter":0.0
    }
  ]
}

Example 1:
LV-Text:
"1. Oberflächenbefestigung Gehwegplatten aufgenommen und wieder eingebaut l=11,00m b=1,98m
 2. Baugraben ... l=10,00m b=0,98m t=1,55m
 3. Druckrohr GGG DN150 eingebaut l=10,00m
 4. Durchstich L=2.0m"

JSON:
{
  "elements": [
    {
      "type": "Oberflächenbefestigung Gehwegplatten",
      "length": 11.0,
      "width": 1.98
    },
    {
      "type": "Baugraben",
      "length": 10.0,
      "width": 0.98,
      "depth": 1.55
    },
    {
      "type": "Druckrohr DN150 GGG",
      "length": 10.0,
      "diameter": 0.15
    },
    {
      "type": "Durchstich",
      "length": 2.0
    },
  ]
}

Example 2:
LV-Text:
"1. Oberflächenbefestigung Gehwegplatten aufgenommen und wieder eingebaut l=11,00m b=1,98m
2. Baugraben im Gehweg angelegt und verbaut. Boden seitlich gelagert, wieder verfüllt und verdichtet l=10,00m b=0,98m t=2,87m
3. Druckrohr GGG DN150 eingebaut l=10,00m
4. Durchstich L=1.5m "

JSON:
{
  "elements": [
    {
      "type": "Oberflächenbefestigung Gehwegplatten",
      "length": 11.0,
      "width": 1.98
    },
    {
      "type": "Baugraben",
      "length": 10.0,
      "width": 0.98,
      "depth": 2.87
    },
    {
      "type": "Druckrohr DN150 GGG",
      "length": 10.0,
      "diameter": 0.15
    },
    {
      "type": "Durchstich",
      "length": 1.5
    }
  ]
}

Example 3:
LV-Text:
"1. Oberflächenbefestigung Mosaikpflaster aufgenommen und wieder eingebaut l=10,20m b=1,98m
2. Baugraben im Gehweg angelegt und verbaut. Boden seitlich gelagert, wieder verfüllt und verdichtet l=10,00m b=0,98m t=1,55m
3. Druckrohr GGG DN150 eingebaut l=10,00m
4. Durchstich L=3.0m "

JSON:
{
  "elements": [
    {
      "type": "Oberflächenbefestigung Mosaikpflaster",
      "length": 10.20,
      "width": 1.18
    },
    {
      "type": "Baugraben",
      "length": 10.0,
      "width": 0.98,
      "depth": 1.55
    },
    {
      "type": "Druckrohr DN150 GGG",
      "length": 10.0,
      "diameter": 0.15
    },
    {
      "type": "Durchstich",
      "length": 3.0
    }
  ]
}

Example 4:
LV-Text:
" 1. Oberflächenbefestigung Mosaikpflaster aufgenommen und wieder eingebaut l=15,20m b=1,29m
2. Baugraben im Gehweg angelegt und verbaut. Boden seitlich gelagert, wieder verfüllt und verdichtet l=15,00m b=1,09m t=4,92m
3. Druckrohr GGG DN150 eingebaut l=15,00m
4. Durchstich L=1.0m"

JSON:
{
  "elements": [
    {
      "type": "Oberflächenbefestigung Mosaikpflaster",
      "length": 15.20,
      "width": 1.29
    },
    {
      "type": "Baugraben",
      "length": 15.0,
      "width": 1.09,
      "depth": 4.92
    },
    {
      "type": "Druckrohr DN150 GGG",
      "length": 15.0,
      "diameter": 0.15
    },
    {
      "type": "Durchstich",
      "length": 1.0
    }
  ]
}

Example 5:
LV-Text:
"1. Oberflächenbefestigung Gehwegplatten aufgenommen und wieder eingebaut l=11,0m b=2,09m
2. Baugraben im Gehweg angelegt und verbaut. Boden seitlich gelagert, wieder verfüllt und verdichtet l=10,00m b=1,09m t=2,87m
3. Druckrohr GGG DN300 eingebaut l=10,00m
4. Durchstich L=3.5m "

JSON:
{
  "elements": [
    {
      "type": "Oberflächenbefestigung Gehwegplatten",
      "length": 11.0,
      "width": 2.09
    },
    {
      "type": "Baugraben",
      "length": 10.0,
      "width": 1.09,
      "depth": 2.87
    },
    {
      "type": "Druckrohr DN300 GGG",
      "length": 10.0,
      "diameter": 0.13
    },
    {
      "type": "Durchstich",
      "length": 3.5
    }
  ]
}

Now parse the following LV text in the exact same JSON format (no extra text, only valid JSON):
"""

# CORS, falls nötig
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"], 
    allow_credentials=True,
    allow_methods=["*"], 
    allow_headers=["*"], 
)

@app.get("/")
def read_root():
    return {"message": "Helloooooo from FastAPI!"}

@app.post("/chat-with-gpt/")
def chat_with_gpt(question: str = Body(..., embed=True)):
    """
    NEUER Endpoint, um ChatGPT (bzw. gpt-3.5-turbo) zu befragen.
    - Bei Themen zu Baugraben, Rohr => Normale Antwort
    - Bei anderen Themen => Limitation + was das KI-Modell kann.
    """

    # Check, ob die Frage "baugraben" oder "rohr" (case-insensitiv) enthält
    lower_q = question.lower()
    if "baugraben" in lower_q or "rohr" in lower_q:
        # => Normale GPT-Antwort
        try:
            response = client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[{"role":"user", "content": question}],
                max_tokens=200,
                temperature=0.2
            )
            answer = response.choices[0].message.content
            return {
                "status": "ok",
                "answer": answer
            }
        except Exception as e:
            return {
                "status":"error",
                "message": f"Fehler bei ChatGPT-Anfrage: {str(e)}"
            }

    else:
        # => Frage liegt außerhalb
        return {
            "status":"limitation",
            "message": (
                "Diese KI ist derzeit auf Baugräben und Rohre spezialisiert. "
                "Andere Themen werden momentan nicht unterstützt.\n\n"
                "Was kann das KI-Modell generieren?\n"
                "- Baugraben-Informationen (Länge, Breite, Tiefe)\n"
                "- Rohr-Informationen (Länge, Durchmesser)\n"
                "- DXF-Dateien mit Bemaßung für Baugräben und Rohre"
            )
        }

# ---------------------------------------------
# /generate-dxf/-Endpunkt:
# ---------------------------------------------
@app.post("/generate-dxf/")
def generate_dxf(lv_text: str = Body(..., embed=True)):
    # JSON über OpenAI-API parsen
    prompt = OPENAI_PROMPT + f"\nLV-Text:\n{lv_text}"
    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=500,
            temperature=0.2
        )
        
        raw_output = response.choices[0].message.content
        parsed_json = json.loads(raw_output)

    except Exception as e:
        return {"status": "error", "message": str(e)}

    try:
        # 2) DXF-Dokument anlegen
        doc = ezdxf.new("R2018", setup=True)
        msp = doc.modelspace()

        doc.header["$LTSCALE"] = 1.0
        doc.header["$CELTSCALE"] = 1.0
        doc.header["$PSLTSCALE"] = 0

        # Linetype "DASHED"
        doc.linetypes.add(
            name="DASHED",
            pattern="A,.5,-.25",
            description="--  --  --  --",
            length=0.75
        )

        # Linetype "DASHED"
        doc.linetypes.add(
            name="DASHDOT",
            pattern="A,.5,-.25,0,-.25",
            description="--  --  --  --",
            length=0.75
        )

        # Layers
        doc.layers.new(name="Baugraben",    dxfattribs={"color":0})
        doc.layers.new(name="InnerRechteck",dxfattribs={"color":0})
        doc.layers.new(name="Zwischenraum", dxfattribs={"color":3})
        doc.layers.new(name="Rohr",         dxfattribs={"color":0})
        doc.layers.new(name="Durchstich",   dxfattribs={"color":0})
        doc.layers.new(name="Oberflaeche",  dxfattribs={"color":0, "linetype":"DASHED"})
        doc.layers.new(name="Symmetrie",    dxfattribs={"color":5, "linetype":"DASHDOT"})

        # 3) Variablen
        bg_length = 0.0
        bg_width  = 0.0
        bg_depth  = 0.0
        r_length  = 0.0
        r_diameter= 0.0
        ds_length = 0.0
        ds_width  = 0.0

        surf_length   = None
        surf_width    = None
        grav_width    = None

        # 4) JSON auslesen
        for element in parsed_json.get("elements", []):
            etype      = element.get("type","").lower()
            length_val = float(element.get("length",   0.0))
            width_val  = float(element.get("width",    0.0))
            depth_val  = float(element.get("depth",    0.0))
            diam_val   = float(element.get("diameter", 0.0))

            if "baugraben" in etype:
                bg_length  = length_val
                bg_width   = width_val
                bg_depth   = depth_val
                grav_width = width_val

            elif "rohr" in etype:
                r_length   = length_val
                r_diameter = diam_val

            elif ("oberflächenbefestigung" in etype 
                or "gehwegplatten" in etype 
                or "mosaikpflaster" in etype 
                or "verbundpflaster" in etype):
                surf_length = length_val
                surf_width  = width_val

                original_type = element.get("type", "")
                
                if "oberflächenbefestigung" in original_type.lower():
                    splitted = original_type.split("befestigung", 1)
                    
                    material = splitted[1].strip() if len(splitted) > 1 else original_type
                    surf_type_text = f"Oberflächenbefestigung: {material}"
                else:
                    surf_type_text = f"Oberflächenbefestigung: {original_type}"
            
            elif "durchstich" in etype:
                ds_length = float(element.get("length", 0.0))
                ds_width  = float(element.get("width",  0.0))

                print(ds_width)
                print(ds_length)

        # =========================
        # VORDERANSICHT (Front)
        # =========================
        outer_front_length = surf_length if surf_length else bg_length
        outer_front_height = surf_width  if surf_width  else bg_width

        outer_points_front = [
          (0, 0),
          (outer_front_length, 0),
          (outer_front_length, outer_front_height),
          (0, outer_front_height),
        ]
        msp.add_lwpolyline(
            outer_points_front, 
            close=True, 
            dxfattribs={"layer": "Baugraben"}
        )

        # Inneres Rechteck => von 0..bg_length (links..rechts), tiefe= bg_depth
        horizontal_diff = outer_front_length - bg_length
        if horizontal_diff < 0:
            horizontal_diff = 0

        vertical_diff = outer_front_height - bg_depth
        if vertical_diff < 0:
            vertical_diff = 0

        offset_x = horizontal_diff / 2
        offset_y = vertical_diff

        inner_front = [
          (offset_x,              offset_y),
          (offset_x + bg_length,  offset_y),
          (offset_x + bg_length,  offset_y + bg_depth),
          (offset_x,              offset_y + bg_depth),
        ]
        msp.add_lwpolyline(
            inner_front,
            close=True,
            dxfattribs={"layer": "InnerRechteck"}
        )

        # -----------------------------------------------
        # Bemaßung: innere Länge (horizontal, unten)
        # -----------------------------------------------
        dim_length_inner_front = msp.add_linear_dim(
            base=(offset_x, offset_y - 1.5),        # Maßlinie etwas tiefer als die Unterkante
            p1=(offset_x,              offset_y),   # Unterkante links
            p2=(offset_x + bg_length,  offset_y),   # Unterkante rechts
            angle=0,
            override={
                "dimtxt": 0.25,
                "dimclrd": 3,
                "dimexe": 0.2,
                "dimexo": 0.2,
                "dimtad": 1,
            }
        )
        dim_length_inner_front.render()

        # -----------------------------------------------
        # Bemaßung: innere Tiefe (vertikal)
        # -----------------------------------------------
        dim_depth_inner = msp.add_linear_dim(
            base=(offset_x + bg_length + 1.5, offset_y),  
            p1=(offset_x + bg_length, offset_y + bg_depth),  
            p2=(offset_x + bg_length, offset_y),
            angle=90,
            override={
                "dimtxt": 0.25,
                "dimclrd": 3,
                "dimexe": 0.2,
                "dimexo": 0.2,
                "dimtad": 1,
            }
        )
        dim_depth_inner.render()

        hatch = msp.add_hatch(color=4,dxfattribs={"layer":"Zwischenraum"})
        hatch.set_pattern_fill("EARTH",scale=0.05)
        hatch.paths.add_polyline_path(outer_points_front,is_closed=True,flags=const.BOUNDARY_PATH_OUTERMOST)
        hatch.paths.add_polyline_path(inner_front,is_closed=True)

        # Rohr
        rohr_height = r_diameter  # z.B. 0.15
        # "untere" Linie => y = offset_y
        rohr_y_bottom = offset_y
        # "obere" Linie => y = offset_y + r_diameter
        rohr_y_top    = offset_y + rohr_height

        rohr_x_left  = offset_x
        rohr_x_right = offset_x + bg_length

        rohr_points_f = [
            (rohr_x_left,  rohr_y_bottom),
            (rohr_x_right, rohr_y_bottom),
            (rohr_x_right, rohr_y_top),
            (rohr_x_left,  rohr_y_top),
        ]
        msp.add_lwpolyline(rohr_points_f, close=True, dxfattribs={"layer": "Rohr"})

        baugraben_x_text = offset_x + bg_length + 5.0
        baugraben_y_text = offset_y + (bg_depth / 2)

        txt_front = f"Baugraben\nL={bg_length}m\nB={bg_width}m\nT={bg_depth}m"
        msp.add_mtext(txt_front, dxfattribs={"layer": "Baugraben", "style": "ISOCPEUR", "char_height": 0.4}
        ).set_location(
            insert=(baugraben_x_text, baugraben_y_text),
            attachment_point=5
        )

        rohr_x_text = rohr_x_right + 3.0
        rohr_y_text = (rohr_y_bottom + rohr_y_top) / 2

        txt_rohr = f"Rohr\nL={r_length}m\nØ={r_diameter}m"
        mtext_rohr = msp.add_mtext(
            txt_rohr,
            dxfattribs={"layer": "Rohr", "style": "ISOCPEUR", "char_height": 0.3},
        )
        mtext_rohr.set_location(
            insert=(rohr_x_text, rohr_y_text),
            attachment_point=5
        )

        # Gestrichelte Symmetrie-Linie im Rohr:
        sym_line_y = (rohr_y_bottom + rohr_y_top) / 2
        msp.add_line(
            (rohr_x_left,  sym_line_y),
            (rohr_x_right, sym_line_y),
            dxfattribs={"layer": "Symmetrie"}
        )

        # === DURCHSTICH ===
        # 1) Mittelpunkt der Vorderansicht:
        # a) X-Koordinaten für den Durchstich (zentriert)
        if ds_length > 0:
          center_x = outer_front_length / 2
          ds_half = ds_length / 2
          ds_left_x = center_x - ds_half
          ds_right_x= center_x + ds_half

          # b) Y-Koordinaten: direkt an der oberen Kante vom äußeren Rechteck
          ds_bottom_y = rohr_y_top + 0.25
          ds_top_y    = outer_front_height
          actual_ds_height = ds_top_y - ds_bottom_y

          # c) Durchstich zeichnen
          durchstich_points_front = [
              (ds_left_x,  ds_bottom_y),
              (ds_right_x, ds_bottom_y),
              (ds_right_x, ds_top_y),
              (ds_left_x,  ds_top_y),
          ]
          msp.add_lwpolyline(
              durchstich_points_front,
              close=True,
              dxfattribs={"layer": "Durchstich"}
          )

          # d) Zweiter Hatch mit derselben EARTH-Schraffur
          hatch_ds = msp.add_hatch(color=4, dxfattribs={"layer": "Durchstich"})
          hatch_ds.set_pattern_fill("EARTH", scale=0.05)
          hatch_ds.paths.add_polyline_path(
              durchstich_points_front,
              is_closed=True,
              flags=const.BOUNDARY_PATH_OUTERMOST
          )

          # 4) Bemaßung (Horizontal)
          msp.add_linear_dim(
              base=(ds_left_x, ds_bottom_y + 1.5),
              p1=(ds_left_x, ds_bottom_y),
              p2=(ds_right_x, ds_bottom_y),
              angle=0,
              override={
                  "dimtxt": 0.25,
                  "dimclrd": 3,
                  "dimexe": 0.2,
                  "dimexo": 0.2,
                  "dimtad": 1,
              }
          ).render()

          # ================================
          # 1) Maßkette: Baugraben (links) -> Durchstich (links)
          # ================================
          msp.add_linear_dim(
              base=(offset_x, ds_bottom_y + 1.5),      # Die "Basislinie" der Maßkette liegt 1.0 unterhalb
              p1=(offset_x, ds_bottom_y),             # Baugraben-Kante links
              p2=(ds_left_x, ds_bottom_y),            # Durchstich-Kante links
              angle=0,                                # Waagerecht messen
              override={
                  "dimtxt": 0.25,
                  "dimclrd": 3,
                  "dimexe": 0.2,
                  "dimexo": 0.2,
                  "dimtad": 1,
              }
          ).render()

          # ================================
          # 2) Maßkette: Durchstich (rechts) -> Baugraben (rechts)
          # ================================
          msp.add_linear_dim(
              base=(ds_right_x, ds_bottom_y + 1.5),    # Basislinie wieder ~1.0 unterhalb
              p1=(ds_right_x, ds_bottom_y),           # Durchstich-Kante rechts
              p2=(offset_x + bg_length, ds_bottom_y), # Baugraben-Kante rechts
              angle=0,
              override={
                  "dimtxt": 0.25,
                  "dimclrd": 3,
                  "dimexe": 0.2,
                  "dimexo": 0.2,
                  "dimtad": 1,
              }
          ).render()

        # =========================
        # DRAUFSICHT (Top)
        # =========================
        # Äußeres Rechteck
        outer_length = surf_length if surf_length else bg_length
        outer_width  = surf_width  if surf_width  else bg_width

        # Inneres Rechteck => Baugraben => grav_width
        inner_width  = grav_width if grav_width else 1.0

        diff_total = outer_width - inner_width
        if diff_total < 0:
            diff_total = 0

        offset = diff_total / 2
        top_view_offset = outer_width + 1.5

        if ds_length <= 0:
          # Äußeres Rechteck => "Oberfläche"
          outer_points_top = [
              (0,              top_view_offset),
              (outer_length,   top_view_offset),
              (outer_length,   top_view_offset + outer_width),
              (0,              top_view_offset + outer_width),
          ]
          msp.add_lwpolyline(outer_points_top, close=True, dxfattribs={"layer":"Oberflaeche"})

          # Inner => offset an jeder Seite
          inner_top = [
              (offset,                     top_view_offset + offset),
              (outer_length - offset,      top_view_offset + offset),
              (outer_length - offset,      top_view_offset + (outer_width - offset)),
              (offset,                     top_view_offset + (outer_width - offset)),
          ]
          msp.add_lwpolyline(inner_top, close=True, dxfattribs={"layer": "InnerRechteck"})

          if surf_type_text:
              # Wir legen den Text in der Mitte ab oder oben:
              x_center = outer_length
              y_center = top_view_offset + outer_width + 0.5
              mtext_oberf = msp.add_mtext(
                  surf_type_text,
                  dxfattribs={"layer":"Oberflaeche", "style": "ISOCPEUR","char_height":0.3}
              )
              mtext_oberf.set_location(
                  insert=(x_center, y_center),
                  attachment_point=5  # MIDDLE_CENTER
              )

          # ---- Maßkette äußeres Rechteck (horizontal) ----
          dim_len_top_outer = msp.add_linear_dim(
              base=(0, top_view_offset - 0.5),   # Maßlinie etwas unterhalb
              p1=(0, top_view_offset),           # Linke untere Ecke
              p2=(outer_length, top_view_offset),# Rechte untere Ecke
              angle=0,
              override={
                  "dimtxt": 0.25,
                  "dimclrd": 3,
                  "dimexe": 0.2,
                  "dimexo": 0.2,
                  "dimtad": 1,
              }
          )
          dim_len_top_outer.render()

          # ---- Maßkette äußeres Rechteck (vertikal) ----
          dim_wid_top_outer = msp.add_linear_dim(
              base=(-1.5, top_view_offset),       # Maßlinie etwas links
              p1=(0, top_view_offset),            # Linke untere Ecke
              p2=(0, top_view_offset + outer_width), # Linke obere Ecke
              angle=90,
              override={
                  "dimtxt": 0.25,
                  "dimclrd": 3,
                  "dimexe": 0.2,
                  "dimexo": 0.2,
                  "dimtad": 1,
              }
          )
          dim_wid_top_outer.render()

          # ---- Maßkette inneres Rechteck (Beispiel hier nur vertikal) ----
          dim_wid_top_inner = msp.add_linear_dim(
              base=((offset - 1), top_view_offset + offset),
              p1=(offset, top_view_offset + offset),
              p2=(offset, top_view_offset + (outer_width - offset)),
              angle=90,
              override={
                  "dimtxt": 0.25,
                  "dimclrd": 3,
                  "dimexe": 0.2,
                  "dimexo": 0.2,
                  "dimtad": 1,
              }
          )
          dim_wid_top_inner.render()
        else:
          rect1_length = (outer_length - ds_length) / 2
          if rect1_length < 0:
              rect1_length = 0
          rect2_length = rect1_length  # gleich lang

          # 2) ============= LINKER BAUGRABEN (x=0..rect1_length) =============
          outer_points_top_left = [
              (0,             top_view_offset),
              (rect1_length,  top_view_offset),
              (rect1_length,  top_view_offset + outer_width),
              (0,             top_view_offset + outer_width),
          ]
          msp.add_lwpolyline(outer_points_top_left, close=True, dxfattribs={"layer":"Oberflaeche"})

          inner_top_left = [
              (offset,                  top_view_offset + offset),
              (rect1_length - offset,   top_view_offset + offset),
              (rect1_length - offset,   top_view_offset + (outer_width - offset)),
              (offset,                  top_view_offset + (outer_width - offset)),
          ]
          msp.add_lwpolyline(inner_top_left, close=True, dxfattribs={"layer": "InnerRechteck"})

          # (Optional) TEXT (Oberflächenbefestigung) beim linken Baugraben
          if surf_type_text:
              x_center_left = rect1_length / 2
              y_center_left = top_view_offset + outer_width + 0.5
              mtext_oberf_left = msp.add_mtext(
                  surf_type_text,
                  dxfattribs={"layer": "Oberflaeche","style": "ISOCPEUR","char_height":0.3}
              )
              mtext_oberf_left.set_location(
                  insert=(x_center_left, y_center_left),
                  attachment_point=5  # MIDDLE_CENTER
              )

          # => Maßketten LINKER Baugraben (outer, inner)
          # (A) Äußeres Rechteck (horizontal)
          dim_len_left_outer = msp.add_linear_dim(
              base=(0, top_view_offset - 0.5),
              p1=(0, top_view_offset),
              p2=(rect1_length, top_view_offset),
              angle=0,
              override={
                  "dimtxt": 0.25,
                  "dimclrd": 3,
                  "dimexe": 0.2,
                  "dimexo": 0.2,
                  "dimtad": 1,
              }
          )
          dim_len_left_outer.render()

          # 4) ============= RECHTER BAUGRABEN =============
          #    Startet bei x_offset_2 = rect1_length + ds_length
          x_offset_2 = rect1_length + ds_length

          outer_points_top_right = [
              (x_offset_2,                  top_view_offset),
              (x_offset_2 + rect2_length,   top_view_offset),
              (x_offset_2 + rect2_length,   top_view_offset + outer_width),
              (x_offset_2,                  top_view_offset + outer_width),
          ]
          msp.add_lwpolyline(
              outer_points_top_right, 
              close=True, 
              dxfattribs={"layer":"Oberflaeche"}
          )

          inner_top_right = [
              (x_offset_2 + offset,                top_view_offset + offset),
              (x_offset_2 + rect2_length - offset, top_view_offset + offset),
              (x_offset_2 + rect2_length - offset, top_view_offset + (outer_width - offset)),
              (x_offset_2 + offset,                top_view_offset + (outer_width - offset)),
          ]
          msp.add_lwpolyline(
              inner_top_right,
              close=True,
              dxfattribs={"layer": "InnerRechteck"}
          )

          # => Maßketten RECHTER Baugraben (outer, inner)
          # (A) Äußeres Rechteck (horizontal)
          dim_len_right_outer = msp.add_linear_dim(
              base=(x_offset_2, top_view_offset - 0.5),
              p1=(x_offset_2,               top_view_offset),
              p2=(x_offset_2 + rect2_length,top_view_offset),
              angle=0,
              override={
                  "dimtxt": 0.25,
                  "dimclrd": 3,
                  "dimexe": 0.2,
                  "dimexo": 0.2,
                  "dimtad": 1,
              }
          )
          dim_len_right_outer.render()

          # ---- Maßkette äußeres Rechteck (vertikal) ----
          dim_wid_top_outer = msp.add_linear_dim(
              base=(-1.5, top_view_offset),       # Maßlinie etwas links
              p1=(0, top_view_offset),            # Linke untere Ecke
              p2=(0, top_view_offset + outer_width), # Linke obere Ecke
              angle=90,
              override={
                  "dimtxt": 0.25,
                  "dimclrd": 3,
                  "dimexe": 0.2,
                  "dimexo": 0.2,
                  "dimtad": 1,
              }
          )
          dim_wid_top_outer.render()

          # ---- Maßkette inneres Rechteck (Beispiel hier nur vertikal) ----
          dim_wid_top_inner = msp.add_linear_dim(
              base=((offset - 1), top_view_offset + offset),
              p1=(offset, top_view_offset + offset),
              p2=(offset, top_view_offset + (outer_width - offset)),
              angle=90,
              override={
                  "dimtxt": 0.25,
                  "dimclrd": 3,
                  "dimexe": 0.2,
                  "dimexo": 0.2,
                  "dimtad": 1,
              }
          )
          dim_wid_top_inner.render()

        # Datei speichern
        file_id = str(uuid.uuid4())
        dxf_filename = f"generated_{file_id}.dxf"
        output_path = os.path.join("temp", dxf_filename)
        os.makedirs("temp", exist_ok=True)
        doc.saveas(output_path)

        return {
            "filename": dxf_filename, 
            "message": "DXF generated successfully"
        }

    except Exception as e:
        return {"status": "error", "message": f"Fehler beim DXF-Generieren: {str(e)}"}

# ---------------------------------------------
# /download-dxf/-Endpunkt:
# ---------------------------------------------
@app.get("/download-dxf/{filename}")
def download_dxf(filename: str):
    file_path = os.path.join("temp", filename)
    if not os.path.exists(file_path):
        return {"error": "File not found"}
    return FileResponse(
        file_path, 
        media_type="application/dxf", 
        filename=filename
    )