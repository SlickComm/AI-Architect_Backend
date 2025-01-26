from fastapi import FastAPI, Body, HTTPException  
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from openai import OpenAI

import ezdxf
from ezdxf.enums import const

import os
from dotenv import load_dotenv
import uuid
import json

app = FastAPI()

# Lädt automatisch die .env-Datei aus dem aktuellen Verzeichnis
load_dotenv()

# OpenAI-Key
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# Global variable for session
session_data = {}

# CORS, falls nötig
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"], 
    allow_credentials=True,
    allow_methods=["*"], 
    allow_headers=["*"], 
)

# -----------------------------------------------------
# 1) START SESSION
# -----------------------------------------------------
@app.post("/start-session/")
def start_session():
    """
    Erzeugt eine neue Session-ID,
    legt in session_data[...] = {"elements":[]} ab,
    und gibt session_id zurück.
    """
    new_session_id = str(uuid.uuid4())
    session_data[new_session_id] = {
        "elements": []
    }
    return {"session_id": new_session_id}

# -----------------------------------------------------
# ADD ELEMENT
# -----------------------------------------------------
@app.post("/add-element/")
def add_element(session_id: str, description: str = Body(..., embed=True)):
    if session_id not in session_data:
        raise HTTPException(status_code=400, detail="Session not found.")

    current_json = session_data[session_id]

    prompt = f"""
ACHTUNG: Füge NICHT eigenmächtig Oberflächenbefestigung hinzu,
außer der Benutzer schreibt ausdrücklich "Oberflächenbefestigung",
"Gehwegplatten", "Mosaikpflaster", "Verbundpflaster" o.Ä.

Wir arbeiten mit diesem JSON-Schema, wobei "material" + "offset" bei Oberflächenbefestigung Pflicht sind:
{{
  "elements": [
    {{
      "type": "string",
      "length": 0.0,
      "width": 0.0,
      "depth": 0.0,
      "diameter": 0.0,

      "material": "",
      "offset": 0.0
    }}
  ],
  "answer": ""
}}

Aktuelles JSON:
{json.dumps(current_json, indent=2)}

ACHTUNG:
- Alle Maßeinheiten sind in Metern.
- Falls in der Beschreibung z.B. "DN150" vorkommt, dann bedeutet das diameter=0.15 (also DN-Wert / 1000).

1) Füge exakt EIN neues Element hinzu basierend auf: "{description}"
   - Falls "Baugraben" => type="Baugraben" + length,width,depth
   - Falls "Rohr" oder "Druckrohr" => type="Rohr" + length, diameter
   - Falls "Oberflächenbefestigung" o.ä. => type="Oberflächenbefestigung",
       *zusätzlich* material="...", offset=... für Randzone, 
       NICHT depth oder diameter benutzen

2) Schreibe zusätzlich ein kurzes "answer"-Feld (1-2 Sätze),
   z.B. als kleine Zusammenfassung dessen, was du generiert hast.

Gib nur das neue komplette JSON zurück. Es soll so aussehen:
{{
  "elements": [...],
  "answer": "Irgendein kurzer Text..."
}}
"""

    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=300,
            temperature=0.1
        )
        raw_output = response.choices[0].message.content
        new_json = json.loads(raw_output)

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Fehler ChatGPT: {str(e)}")

    # Die neue JSON-Struktur (elements + answer) in der Session speichern
    session_data[session_id] = {
      "elements": new_json["elements"]
    }

    # Dann an den Client beides zurücksenden
    return {
      "status": "ok",
      "updated_json": session_data[session_id],
      "answer": new_json.get("answer", "")
    }

# -----------------------------------------------------
# GENERATE DXF
# -----------------------------------------------------
@app.post("/generate-dxf-by-session/")
def generate_dxf_by_session(session_id: str):
    if session_id not in session_data:
        raise HTTPException(status_code=400, detail="Session not found.")

    current_json = session_data[session_id]

    try:
        dxf_file_path = _generate_dxf_intern(current_json)
        return FileResponse(
            dxf_file_path,
            media_type="application/dxf",
            filename=os.path.basename(dxf_file_path),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

def _generate_dxf_intern(parsed_json) -> str:
    # 2) DXF-Dokument anlegen
        doc = ezdxf.new("R2018", setup=True)
        msp = doc.modelspace()

        doc.header["$LTSCALE"] = 1.0
        doc.header["$CELTSCALE"] = 1.0
        doc.header["$PSLTSCALE"] = 0

        # Linetypes
        doc.linetypes.add(
            name="DASHED",
            pattern="A,.5,-.25",
            description="--  --  --  --",
            length=0.75
        )
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
        doc.layers.new(name="Rohr",         dxfattribs={"color": 0})
        doc.layers.new(name="Oberflaeche",  dxfattribs={"color":0, "linetype":"DASHED"})
        doc.layers.new(name="Symmetrie",    dxfattribs={"color":5, "linetype":"DASHDOT"})

        # Variablen (Baugraben)
        bg_length = 0.0
        bg_width  = 0.0
        bg_depth  = 0.0

        # Variablen (Rohr)
        r_length   = 0.0
        r_diameter = 0.0

        # NEU: Randzone für Oberflächenbefestigung
        surf_offset = 0.0
        surf_material = ""
        surf_text   = None

        # JSON auslesen
        for element in parsed_json.get("elements", []):
            etype      = element.get("type","").lower()
            length_val = float(element.get("length", 0.0))
            width_val  = float(element.get("width",  0.0))
            depth_val  = float(element.get("depth",  0.0))
            diam_val = float(element.get("diameter", 0.0))

            # *** NEUE Felder, falls existieren ***
            mat_val     = element.get("material", "")
            offset_val  = float(element.get("offset", 0.0))

            if "baugraben" in etype:
                bg_length = length_val
                bg_width  = width_val
                bg_depth  = depth_val

            elif ("druckrohr" in etype) or ("rohr" in etype):
                r_length   = length_val
                r_diameter = diam_val

            elif "oberflächenbefestigung" in etype or "oberfläche" in etype:
                # => offset + material
                surf_offset   = offset_val
                surf_material = mat_val if mat_val else "unbekannt"
                # Komponiere einen Anzeigenamen
                surf_text     = f"Oberflächenbefestigung: {surf_material}"

        # =========================
        # VORDERANSICHT (Front)
        # =========================
        LEFT_RIGHT_OFFSET = 0.2
        BOTTOM_OFFSET     = 0.2

        min_width_for_baugraben  = bg_length + 2 * LEFT_RIGHT_OFFSET
        min_height_for_baugraben = bg_depth + BOTTOM_OFFSET

        outer_front_length = min_width_for_baugraben
        outer_front_height = min_height_for_baugraben

        # Äußeres Rechteck
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

        # Inneres Rechteck = eigentlicher Baugraben
        offset_x = LEFT_RIGHT_OFFSET
        offset_y = BOTTOM_OFFSET
        inner_front = [
            (offset_x,                offset_y),
            (offset_x + bg_length,    offset_y),
            (offset_x + bg_length,    offset_y + bg_depth),
            (offset_x,                offset_y + bg_depth),
        ]
        msp.add_lwpolyline(
            inner_front,
            close=True,
            dxfattribs={"layer": "InnerRechteck"}
        )

        # Bemaßung: Länge (horizontal)
        dim_length = msp.add_linear_dim(
            base=(offset_x, offset_y - 1.0),
            p1=(offset_x, offset_y),
            p2=(offset_x + bg_length, offset_y),
            angle=0,
            override={
                "dimtxt": 0.25,
                "dimclrd": 3,
                "dimexe": 0.2,
                "dimexo": 0.2,
                "dimtad": 1,
            }
        )
        dim_length.render()

        # Bemaßung: Tiefe (vertikal)
        dim_depth = msp.add_linear_dim(
            base=(offset_x + bg_length + 1.0, offset_y),
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
        dim_depth.render()

        # Zwischenraum-Hatch
        hatch = msp.add_hatch(color=4, dxfattribs={"layer": "Zwischenraum"})
        hatch.set_pattern_fill("EARTH", scale=0.05)
        hatch.paths.add_polyline_path(outer_points_front,  is_closed=True)
        hatch.paths.add_polyline_path(inner_front,         is_closed=True)

        # =========================
        # ROHR / DRUCKROHR in Vorderansicht
        # =========================
        # Nur zeichnen, wenn r_diameter > 0
        if r_length > 0 or r_diameter > 0:
            rohr_y_bottom = offset_y
            rohr_y_top    = offset_y + r_diameter

            rohr_x_left  = offset_x
            rohr_x_right = offset_x + bg_length

            rohr_points_f = [
                (rohr_x_left,  rohr_y_bottom),
                (rohr_x_right, rohr_y_bottom),
                (rohr_x_right, rohr_y_top),
                (rohr_x_left,  rohr_y_top),
            ]
            msp.add_lwpolyline(
                rohr_points_f,
                close=True,
                dxfattribs={"layer": "Rohr"}
            )

            # Symmetrie-Linie im Rohr (wenn du willst)
            sym_line_y = (rohr_y_bottom + rohr_y_top) / 2
            msp.add_line(
                (rohr_x_left,  sym_line_y),
                (rohr_x_right, sym_line_y),
                dxfattribs={"layer": "Symmetrie"}
            )

        # =========================
        # DRAUFSICHT (Top)
        # =========================
        top_view_offset = outer_front_height + 1.5
        top_left_x = offset_x
        top_left_y = top_view_offset + offset_y

        outer_points_top = [
            (top_left_x,                 top_left_y),
            (top_left_x + bg_length,     top_left_y),
            (top_left_x + bg_length,     top_left_y + bg_width),
            (top_left_x,                 top_left_y + bg_width),
        ]
        msp.add_lwpolyline(
            outer_points_top,
            close=True,
            dxfattribs={"layer": "Baugraben"},
        )

        # Bemaßung Draufsicht: horizontale
        dim_len_top = msp.add_linear_dim(
            base=(top_left_x, top_left_y - 1.0),
            p1=(top_left_x, top_left_y),
            p2=(top_left_x + bg_length, top_left_y),
            angle=0,
            override={
                "dimtxt": 0.25,
                "dimclrd": 3,
                "dimexe": 0.2,
                "dimexo": 0.2,
                "dimtad": 1,
            }
        )
        dim_len_top.render()

        # Bemaßung Draufsicht: vertikale
        dim_wid_top = msp.add_linear_dim(
            base=(top_left_x - 1.5, top_left_y),
            p1=(top_left_x, top_left_y),
            p2=(top_left_x, top_left_y + bg_width),
            angle=90,
            override={
                "dimtxt": 0.25,
                "dimclrd": 3,
                "dimexe": 0.2,
                "dimexo": 0.2,
                "dimtad": 1,
            }
        )
        dim_wid_top.render()

        # ===============================
        # OBERFLÄCHENBEFESTIGUNG ("Randzone")
        # ===============================
        # Prüfen, ob surf_offset > 0 => Dann zeichnen
        if surf_offset > 0:
            surf_left   = top_left_x - surf_offset
            surf_right  = top_left_x + bg_length + surf_offset
            surf_bottom = top_left_y - surf_offset
            surf_top    = top_left_y + bg_width  + surf_offset

            outer_surface_top = [
                (top_left_x - surf_offset,                  top_left_y - surf_offset),
                (top_left_x + bg_length + surf_offset,      top_left_y - surf_offset),
                (top_left_x + bg_length + surf_offset,      top_left_y + bg_width + surf_offset),
                (top_left_x - surf_offset,                  top_left_y + bg_width + surf_offset),
            ]
            msp.add_lwpolyline(
                outer_surface_top, 
                close=True,
                dxfattribs={"layer": "Oberflaeche"}
            )

            # Ggf. Text für Oberflächenbefestigung
            if surf_text:
                x_text = top_left_x + (bg_length / 2.0)  # Mitte horizontal
                y_text = top_left_y + bg_width + surf_offset + 0.5

                mtext_surf = msp.add_mtext(
                    surf_text,
                    dxfattribs={
                        "layer": "Oberflaeche",
                        "style": "ISOCPEUR",
                        "char_height": 0.3
                    },
                )
                mtext_surf.set_location(insert=(x_text, y_text), attachment_point=5)

            # 4) VERTIKAL dimension (Maßkette)
            dim_surf_wid = msp.add_linear_dim(
                base=(surf_left - 2.0, surf_bottom),
                p1=(surf_left, surf_bottom),          
                p2=(surf_left, surf_top),             
                angle=90,                             
                override={
                    "dimtxt": 0.25,
                    "dimclrd": 3,
                    "dimexe": 0.2,
                    "dimexo": 0.2,
                    "dimtad": 1,
                }
            )
            dim_surf_wid.render()

        # =========================
        # Aufmaß-Text
        # =========================
        aufmass_text = "Aufmaß:\n"
        aufmass_text += f"1) Baugraben:\n   L={bg_length} m, B={bg_width} m, T={bg_depth} m\n"

        if (r_length > 0 or r_diameter > 0):
            aufmass_text += f"2) Rohr:\n   L={r_length} m, Ø={r_diameter} m\n"

        # Oberflächenbefestigung
        if surf_offset > 0:
            aufmass_text += f"3) Oberflächenbefestigung:\n   Randzone: {surf_offset} m\n"
            if surf_material:
                aufmass_text += f"   Material: {surf_material}\n"

        # z. B. links unten
        text_x = 0.0
        text_y = -3.0

        mtext_aufmass = msp.add_mtext(
            aufmass_text,
            dxfattribs={
                "layer": "Baugraben",
                "style": "ISOCPEUR",
                "char_height": 0.3
            }
        )
        mtext_aufmass.set_location(insert=(text_x, text_y), attachment_point=1)

        file_id = str(uuid.uuid4())
        dxf_filename = f"generated_{file_id}.dxf"
        output_path = os.path.join("temp", dxf_filename)
        os.makedirs("temp", exist_ok=True)
        doc.saveas(output_path)

        doc.saveas(output_path)
        return output_path

# -----------------------------------------------------
# Edit Element
# -----------------------------------------------------
@app.post("/edit-element/")
def edit_element(
    session_id: str,
    instruction: str = Body(..., embed=True)
):

    if session_id not in session_data:
        raise HTTPException(status_code=400, detail="Session not found.")

    current_json = session_data[session_id]

    # Hier das prompt an ChatGPT formulieren
    prompt = f"""
Wir haben dieses JSON:
{json.dumps(current_json, indent=2)}

Aufgabe:
1) Suche das Element, das zu "{instruction}" passt.
2) Ändere NUR die relevanten Felder (length, width, depth, diameter, offset, material, …).
3) Lösche KEIN weiteres Element (außer es wurde ausdrücklich gewünscht).
4) Gib das fertige JSON zurück (elements + answer).
   answer=1-2 Sätze, was du geändert hast.
"""
    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=500,
            temperature=0.0,
        )
        raw_output = response.choices[0].message.content
        new_json = json.loads(raw_output)

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Fehler ChatGPT: {str(e)}")

    # JSON übernehmen
    session_data[session_id] = {
      "elements": new_json["elements"]
    }

    return {
      "status": "ok",
      "updated_json": session_data[session_id],
      "answer": new_json.get("answer", "")
    }

# -----------------------------------------------------
# Delete Element
# -----------------------------------------------------
@app.post("/remove-element/")
def remove_element(
    session_id: str,
    instruction: str = Body(..., embed=True)
):
    """
    Beispiel-Instruktion: "Lösche das Rohr."
    oder "Entferne die Oberflächenbefestigung Gehwegplatten."
    """

    if session_id not in session_data:
        raise HTTPException(status_code=400, detail="Session not found.")

    current_json = session_data[session_id]

    prompt = f"""
Aktuelles JSON:
{json.dumps(current_json, indent=2)}

1) Finde EIN Element, das zu "{instruction}" passt.
2) Lösche es aus dem JSON.
3) Gib das komplette JSON zurück plus "answer"-Feld:
   "answer": "Was du getan hast."
"""

    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=300,
            temperature=0.0
        )
        raw_output = response.choices[0].message.content
        new_json = json.loads(raw_output)

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Fehler ChatGPT: {str(e)}")

    session_data[session_id] = {
      "elements": new_json["elements"]
    }

    return {
      "status": "ok",
      "updated_json": session_data[session_id],
      "answer": new_json.get("answer", "")
    }

# ---------------------------------------------
# /generate-dxf/-Endpunkt:
# ---------------------------------------------
# @app.post("/generate-dxf/")
# def generate_dxf(lv_text: str = Body(..., embed=True)):
#     # JSON über OpenAI-API parsen
#     prompt = OPENAI_PROMPT + f"\nLV-Text:\n{lv_text}"
#     try:
#         response = client.chat.completions.create(
#             model="gpt-3.5-turbo",
#             messages=[{"role": "user", "content": prompt}],
#             max_tokens=500,
#             temperature=0.2
#         )
        
#         raw_output = response.choices[0].message.content
#         parsed_json = json.loads(raw_output)

#     except Exception as e:
#         return {"status": "error", "message": str(e)}

#     try:
#         # 2) DXF-Dokument anlegen
#         doc = ezdxf.new("R2018", setup=True)
#         msp = doc.modelspace()

#         doc.header["$LTSCALE"] = 1.0
#         doc.header["$CELTSCALE"] = 1.0
#         doc.header["$PSLTSCALE"] = 0

#         # Linetype "DASHED"
#         doc.linetypes.add(
#             name="DASHED",
#             pattern="A,.5,-.25",
#             description="--  --  --  --",
#             length=0.75
#         )

#         # Linetype "DASHED"
#         doc.linetypes.add(
#             name="DASHDOT",
#             pattern="A,.5,-.25,0,-.25",
#             description="--  --  --  --",
#             length=0.75
#         )

#         # Layers
#         doc.layers.new(name="Baugraben",    dxfattribs={"color":0})
#         doc.layers.new(name="InnerRechteck",dxfattribs={"color":0})
#         doc.layers.new(name="Zwischenraum", dxfattribs={"color":3})
#         doc.layers.new(name="Rohr",         dxfattribs={"color":0})
#         doc.layers.new(name="Durchstich",   dxfattribs={"color":0})
#         doc.layers.new(name="Oberflaeche",  dxfattribs={"color":0, "linetype":"DASHED"})
#         doc.layers.new(name="Symmetrie",    dxfattribs={"color":5, "linetype":"DASHDOT"})

#         # 3) Variablen
#         bg_length = 0.0
#         bg_width  = 0.0
#         bg_depth  = 0.0
#         r_length  = 0.0
#         r_diameter= 0.0
#         ds_length = 0.0
#         ds_width  = 0.0

#         surf_length   = None
#         surf_width    = None
#         grav_width    = None

#         # 4) JSON auslesen
#         for element in parsed_json.get("elements", []):
#             etype      = element.get("type","").lower()
#             length_val = float(element.get("length",   0.0))
#             width_val  = float(element.get("width",    0.0))
#             depth_val  = float(element.get("depth",    0.0))
#             diam_val   = float(element.get("diameter", 0.0))

#             if "baugraben" in etype:
#                 bg_length  = length_val
#                 bg_width   = width_val
#                 bg_depth   = depth_val
#                 grav_width = width_val

#             elif "rohr" in etype:
#                 r_length   = length_val
#                 r_diameter = diam_val

#             elif ("oberflächenbefestigung" in etype 
#                 or "gehwegplatten" in etype 
#                 or "mosaikpflaster" in etype 
#                 or "verbundpflaster" in etype):
#                 surf_length = length_val
#                 surf_width  = width_val

#                 original_type = element.get("type", "")
                
#                 if "oberflächenbefestigung" in original_type.lower():
#                     splitted = original_type.split("befestigung", 1)
                    
#                     material = splitted[1].strip() if len(splitted) > 1 else original_type
#                     surf_type_text = f"Oberflächenbefestigung: {material}"
#                 else:
#                     surf_type_text = f"Oberflächenbefestigung: {original_type}"
            
#             elif "durchstich" in etype:
#                 ds_length = float(element.get("length", 0.0))
#                 ds_width  = float(element.get("width",  0.0))

#                 print(ds_width)
#                 print(ds_length)

#         # =========================
#         # VORDERANSICHT (Front)
#         # =========================
#         LEFT_RIGHT_OFFSET = 0.2
#         BOTTOM_OFFSET     = 0.2

#         min_width_for_baugraben = bg_length + 2*LEFT_RIGHT_OFFSET
#         min_height_for_baugraben= bg_depth + BOTTOM_OFFSET

#         # outer_front_length = max(surf_length, min_width_for_baugraben)
#         # if surf_length is None, we use min_width_for_baugraben
#         outer_front_length = 0.0
#         if surf_length is not None:
#             outer_front_length = max(surf_length, min_width_for_baugraben)
#         else:
#             outer_front_length = min_width_for_baugraben

#         # outer_front_height = max(surf_width, min_height_for_baugraben)
#         outer_front_height = 0.0
#         if surf_width is not None:
#             outer_front_height = max(surf_width, min_height_for_baugraben)
#         else:
#             outer_front_height = min_height_for_baugraben

#         # Äußeres Rechteck (Front)
#         outer_points_front = [
#             (0, 0),
#             (outer_front_length, 0),
#             (outer_front_length, outer_front_height),
#             (0, outer_front_height),
#         ]
#         msp.add_lwpolyline(
#             outer_points_front,
#             close=True,
#             dxfattribs={"layer": "Baugraben"}
#         )

#         # (B) Inneres Rechteck (Baugraben):
#         # offset_x= 0.5, offset_y= 0.5
#         # size = bg_length x bg_depth
#         offset_x = LEFT_RIGHT_OFFSET
#         offset_y = BOTTOM_OFFSET

#         inner_front = [
#             (offset_x,               offset_y),
#             (offset_x + bg_length,   offset_y),
#             (offset_x + bg_length,   offset_y + bg_depth),
#             (offset_x,               offset_y + bg_depth),
#         ]
#         msp.add_lwpolyline(
#             inner_front,
#             close=True,
#             dxfattribs={"layer": "InnerRechteck"}
#         )

#         # Bemaßung (Länge)
#         dim_length_inner_front = msp.add_linear_dim(
#             base=(offset_x, offset_y - 1.0),
#             p1=(offset_x, offset_y),
#             p2=(offset_x + bg_length, offset_y),
#             angle=0,
#             override={
#                 "dimtxt": 0.25,
#                 "dimclrd": 3,
#                 "dimexe": 0.2,
#                 "dimexo": 0.2,
#                 "dimtad": 1,
#             }
#         )
#         dim_length_inner_front.render()

#         # Bemaßung (Tiefe)
#         dim_depth_inner = msp.add_linear_dim(
#             base=(offset_x + bg_length + 1.0, offset_y),
#             p1=(offset_x + bg_length, offset_y + bg_depth),
#             p2=(offset_x + bg_length, offset_y),
#             angle=90,
#             override={
#                 "dimtxt": 0.25,
#                 "dimclrd": 3,
#                 "dimexe": 0.2,
#                 "dimexo": 0.2,
#                 "dimtad": 1,
#             }
#         )
#         dim_depth_inner.render()

#         hatch = msp.add_hatch(color=4,dxfattribs={"layer":"Zwischenraum"})
#         hatch.set_pattern_fill("EARTH",scale=0.05)
#         hatch.paths.add_polyline_path(outer_points_front,is_closed=True,flags=const.BOUNDARY_PATH_OUTERMOST)
#         hatch.paths.add_polyline_path(inner_front,is_closed=True)

#         # Rohr
#         rohr_height = r_diameter  # z.B. 0.15
#         # "untere" Linie => y = offset_y
#         rohr_y_bottom = offset_y
#         # "obere" Linie => y = offset_y + r_diameter
#         rohr_y_top    = offset_y + rohr_height

#         rohr_x_left  = offset_x
#         rohr_x_right = offset_x + bg_length

#         rohr_points_f = [
#             (rohr_x_left,  rohr_y_bottom),
#             (rohr_x_right, rohr_y_bottom),
#             (rohr_x_right, rohr_y_top),
#             (rohr_x_left,  rohr_y_top),
#         ]
#         msp.add_lwpolyline(rohr_points_f, close=True, dxfattribs={"layer": "Rohr"})

#         baugraben_x_text = offset_x + bg_length + 5.0
#         baugraben_y_text = offset_y + (bg_depth / 2)

#         txt_front = f"Baugraben\nL={bg_length}m\nB={bg_width}m\nT={bg_depth}m"
#         msp.add_mtext(txt_front, dxfattribs={"layer": "Baugraben", "style": "ISOCPEUR", "char_height": 0.4}
#         ).set_location(
#             insert=(baugraben_x_text, baugraben_y_text),
#             attachment_point=5
#         )

#         rohr_x_text = rohr_x_right + 3.0
#         rohr_y_text = (rohr_y_bottom + rohr_y_top) / 2

#         txt_rohr = f"Rohr\nL={r_length}m\nØ={r_diameter}m"
#         mtext_rohr = msp.add_mtext(
#             txt_rohr,
#             dxfattribs={"layer": "Rohr", "style": "ISOCPEUR", "char_height": 0.3},
#         )
#         mtext_rohr.set_location(
#             insert=(rohr_x_text, rohr_y_text),
#             attachment_point=5
#         )

#         # Gestrichelte Symmetrie-Linie im Rohr:
#         sym_line_y = (rohr_y_bottom + rohr_y_top) / 2
#         msp.add_line(
#             (rohr_x_left,  sym_line_y),
#             (rohr_x_right, sym_line_y),
#             dxfattribs={"layer": "Symmetrie"}
#         )

#         # === DURCHSTICH ===
#         # 1) Mittelpunkt der Vorderansicht:
#         # a) X-Koordinaten für den Durchstich (zentriert)
#         if ds_length > 0:
#           center_x = outer_front_length / 2
#           ds_half = ds_length / 2
#           ds_left_x = center_x - ds_half
#           ds_right_x= center_x + ds_half

#           # b) Y-Koordinaten: direkt an der oberen Kante vom äußeren Rechteck
#           ds_bottom_y = rohr_y_top + 0.25
#           ds_top_y    = outer_front_height
#           actual_ds_height = ds_top_y - ds_bottom_y

#           # c) Durchstich zeichnen
#           durchstich_points_front = [
#               (ds_left_x,  ds_bottom_y),
#               (ds_right_x, ds_bottom_y),
#               (ds_right_x, ds_top_y),
#               (ds_left_x,  ds_top_y),
#           ]
#           msp.add_lwpolyline(
#               durchstich_points_front,
#               close=True,
#               dxfattribs={"layer": "Durchstich"}
#           )

#           # d) Zweiter Hatch mit derselben EARTH-Schraffur
#           hatch_ds = msp.add_hatch(color=4, dxfattribs={"layer": "Durchstich"})
#           hatch_ds.set_pattern_fill("EARTH", scale=0.05)
#           hatch_ds.paths.add_polyline_path(
#               durchstich_points_front,
#               is_closed=True,
#               flags=const.BOUNDARY_PATH_OUTERMOST
#           )

#           # 4) Bemaßung (Horizontal)
#           msp.add_linear_dim(
#               base=(ds_left_x, ds_bottom_y + 1.5),
#               p1=(ds_left_x, ds_bottom_y),
#               p2=(ds_right_x, ds_bottom_y),
#               angle=0,
#               override={
#                   "dimtxt": 0.25,
#                   "dimclrd": 3,
#                   "dimexe": 0.2,
#                   "dimexo": 0.2,
#                   "dimtad": 1,
#               }
#           ).render()

#           # ================================
#           # 1) Maßkette: Baugraben (links) -> Durchstich (links)
#           # ================================
#           msp.add_linear_dim(
#               base=(offset_x, ds_bottom_y + 1.5),      # Die "Basislinie" der Maßkette liegt 1.0 unterhalb
#               p1=(offset_x, ds_bottom_y),             # Baugraben-Kante links
#               p2=(ds_left_x, ds_bottom_y),            # Durchstich-Kante links
#               angle=0,                                # Waagerecht messen
#               override={
#                   "dimtxt": 0.25,
#                   "dimclrd": 3,
#                   "dimexe": 0.2,
#                   "dimexo": 0.2,
#                   "dimtad": 1,
#               }
#           ).render()

#           # ================================
#           # 2) Maßkette: Durchstich (rechts) -> Baugraben (rechts)
#           # ================================
#           msp.add_linear_dim(
#               base=(ds_right_x, ds_bottom_y + 1.5),    # Basislinie wieder ~1.0 unterhalb
#               p1=(ds_right_x, ds_bottom_y),           # Durchstich-Kante rechts
#               p2=(offset_x + bg_length, ds_bottom_y), # Baugraben-Kante rechts
#               angle=0,
#               override={
#                   "dimtxt": 0.25,
#                   "dimclrd": 3,
#                   "dimexe": 0.2,
#                   "dimexo": 0.2,
#                   "dimtad": 1,
#               }
#           ).render()

#         # =========================
#         # DRAUFSICHT (Top)
#         # =========================
#         # Äußeres Rechteck
#         outer_length = surf_length if surf_length else bg_length
#         outer_width  = surf_width  if surf_width  else bg_width

#         # Inneres Rechteck => Baugraben => grav_width
#         inner_width  = grav_width if grav_width else 1.0

#         diff_total = outer_width - inner_width
#         if diff_total < 0:
#             diff_total = 0

#         offset = diff_total / 2
#         top_view_offset = outer_width + 1.5

#         if ds_length <= 0:
#           # Äußeres Rechteck => "Oberfläche"
#           outer_points_top = [
#               (0,              top_view_offset),
#               (outer_length,   top_view_offset),
#               (outer_length,   top_view_offset + outer_width),
#               (0,              top_view_offset + outer_width),
#           ]
#           msp.add_lwpolyline(outer_points_top, close=True, dxfattribs={"layer":"Oberflaeche"})

#           # Inner => offset an jeder Seite
#           inner_top = [
#               (offset,                     top_view_offset + offset),
#               (outer_length - offset,      top_view_offset + offset),
#               (outer_length - offset,      top_view_offset + (outer_width - offset)),
#               (offset,                     top_view_offset + (outer_width - offset)),
#           ]
#           msp.add_lwpolyline(inner_top, close=True, dxfattribs={"layer": "InnerRechteck"})

#           if surf_type_text:
#               # Wir legen den Text in der Mitte ab oder oben:
#               x_center = outer_length
#               y_center = top_view_offset + outer_width + 0.5
#               mtext_oberf = msp.add_mtext(
#                   surf_type_text,
#                   dxfattribs={"layer":"Oberflaeche", "style": "ISOCPEUR","char_height":0.3}
#               )
#               mtext_oberf.set_location(
#                   insert=(x_center, y_center),
#                   attachment_point=5  # MIDDLE_CENTER
#               )

#           # ---- Maßkette äußeres Rechteck (horizontal) ----
#           dim_len_top_outer = msp.add_linear_dim(
#               base=(0, top_view_offset - 0.5),   # Maßlinie etwas unterhalb
#               p1=(0, top_view_offset),           # Linke untere Ecke
#               p2=(outer_length, top_view_offset),# Rechte untere Ecke
#               angle=0,
#               override={
#                   "dimtxt": 0.25,
#                   "dimclrd": 3,
#                   "dimexe": 0.2,
#                   "dimexo": 0.2,
#                   "dimtad": 1,
#               }
#           )
#           dim_len_top_outer.render()

#           # ---- Maßkette äußeres Rechteck (vertikal) ----
#           dim_wid_top_outer = msp.add_linear_dim(
#               base=(-1.5, top_view_offset),       # Maßlinie etwas links
#               p1=(0, top_view_offset),            # Linke untere Ecke
#               p2=(0, top_view_offset + outer_width), # Linke obere Ecke
#               angle=90,
#               override={
#                   "dimtxt": 0.25,
#                   "dimclrd": 3,
#                   "dimexe": 0.2,
#                   "dimexo": 0.2,
#                   "dimtad": 1,
#               }
#           )
#           dim_wid_top_outer.render()

#           # ---- Maßkette inneres Rechteck (Beispiel hier nur vertikal) ----
#           dim_wid_top_inner = msp.add_linear_dim(
#               base=((offset - 1), top_view_offset + offset),
#               p1=(offset, top_view_offset + offset),
#               p2=(offset, top_view_offset + (outer_width - offset)),
#               angle=90,
#               override={
#                   "dimtxt": 0.25,
#                   "dimclrd": 3,
#                   "dimexe": 0.2,
#                   "dimexo": 0.2,
#                   "dimtad": 1,
#               }
#           )
#           dim_wid_top_inner.render()
#         else:
#           rect1_length = (outer_length - ds_length) / 2
#           if rect1_length < 0:
#               rect1_length = 0
#           rect2_length = rect1_length  # gleich lang

#           # 2) ============= LINKER BAUGRABEN (x=0..rect1_length) =============
#           outer_points_top_left = [
#               (0,             top_view_offset),
#               (rect1_length,  top_view_offset),
#               (rect1_length,  top_view_offset + outer_width),
#               (0,             top_view_offset + outer_width),
#           ]
#           msp.add_lwpolyline(outer_points_top_left, close=True, dxfattribs={"layer":"Oberflaeche"})

#           inner_top_left = [
#               (offset,                  top_view_offset + offset),
#               (rect1_length - offset,   top_view_offset + offset),
#               (rect1_length - offset,   top_view_offset + (outer_width - offset)),
#               (offset,                  top_view_offset + (outer_width - offset)),
#           ]
#           msp.add_lwpolyline(inner_top_left, close=True, dxfattribs={"layer": "InnerRechteck"})

#           # (Optional) TEXT (Oberflächenbefestigung) beim linken Baugraben
#           if surf_type_text:
#               x_center_left = rect1_length / 2
#               y_center_left = top_view_offset + outer_width + 0.5
#               mtext_oberf_left = msp.add_mtext(
#                   surf_type_text,
#                   dxfattribs={"layer": "Oberflaeche","style": "ISOCPEUR","char_height":0.3}
#               )
#               mtext_oberf_left.set_location(
#                   insert=(x_center_left, y_center_left),
#                   attachment_point=5  # MIDDLE_CENTER
#               )

#           # => Maßketten LINKER Baugraben (outer, inner)
#           # (A) Äußeres Rechteck (horizontal)
#           dim_len_left_outer = msp.add_linear_dim(
#               base=(0, top_view_offset - 0.5),
#               p1=(0, top_view_offset),
#               p2=(rect1_length, top_view_offset),
#               angle=0,
#               override={
#                   "dimtxt": 0.25,
#                   "dimclrd": 3,
#                   "dimexe": 0.2,
#                   "dimexo": 0.2,
#                   "dimtad": 1,
#               }
#           )
#           dim_len_left_outer.render()

#           # 4) ============= RECHTER BAUGRABEN =============
#           #    Startet bei x_offset_2 = rect1_length + ds_length
#           x_offset_2 = rect1_length + ds_length

#           outer_points_top_right = [
#               (x_offset_2,                  top_view_offset),
#               (x_offset_2 + rect2_length,   top_view_offset),
#               (x_offset_2 + rect2_length,   top_view_offset + outer_width),
#               (x_offset_2,                  top_view_offset + outer_width),
#           ]
#           msp.add_lwpolyline(
#               outer_points_top_right, 
#               close=True, 
#               dxfattribs={"layer":"Oberflaeche"}
#           )

#           inner_top_right = [
#               (x_offset_2 + offset,                top_view_offset + offset),
#               (x_offset_2 + rect2_length - offset, top_view_offset + offset),
#               (x_offset_2 + rect2_length - offset, top_view_offset + (outer_width - offset)),
#               (x_offset_2 + offset,                top_view_offset + (outer_width - offset)),
#           ]
#           msp.add_lwpolyline(
#               inner_top_right,
#               close=True,
#               dxfattribs={"layer": "InnerRechteck"}
#           )

#           # => Maßketten RECHTER Baugraben (outer, inner)
#           # (A) Äußeres Rechteck (horizontal)
#           dim_len_right_outer = msp.add_linear_dim(
#               base=(x_offset_2, top_view_offset - 0.5),
#               p1=(x_offset_2,               top_view_offset),
#               p2=(x_offset_2 + rect2_length,top_view_offset),
#               angle=0,
#               override={
#                   "dimtxt": 0.25,
#                   "dimclrd": 3,
#                   "dimexe": 0.2,
#                   "dimexo": 0.2,
#                   "dimtad": 1,
#               }
#           )
#           dim_len_right_outer.render()

#           # ---- Maßkette äußeres Rechteck (vertikal) ----
#           dim_wid_top_outer = msp.add_linear_dim(
#               base=(-1.5, top_view_offset),       # Maßlinie etwas links
#               p1=(0, top_view_offset),            # Linke untere Ecke
#               p2=(0, top_view_offset + outer_width), # Linke obere Ecke
#               angle=90,
#               override={
#                   "dimtxt": 0.25,
#                   "dimclrd": 3,
#                   "dimexe": 0.2,
#                   "dimexo": 0.2,
#                   "dimtad": 1,
#               }
#           )
#           dim_wid_top_outer.render()

#           # ---- Maßkette inneres Rechteck (Beispiel hier nur vertikal) ----
#           dim_wid_top_inner = msp.add_linear_dim(
#               base=((offset - 1), top_view_offset + offset),
#               p1=(offset, top_view_offset + offset),
#               p2=(offset, top_view_offset + (outer_width - offset)),
#               angle=90,
#               override={
#                   "dimtxt": 0.25,
#                   "dimclrd": 3,
#                   "dimexe": 0.2,
#                   "dimexo": 0.2,
#                   "dimtad": 1,
#               }
#           )
#           dim_wid_top_inner.render()

#         # Datei speichern
#         file_id = str(uuid.uuid4())
#         dxf_filename = f"generated_{file_id}.dxf"
#         output_path = os.path.join("temp", dxf_filename)
#         os.makedirs("temp", exist_ok=True)
#         doc.saveas(output_path)

#         return {
#             "filename": dxf_filename, 
#             "message": "DXF generated successfully"
#         }

#     except Exception as e:
#         return {"status": "error", "message": f"Fehler beim DXF-Generieren: {str(e)}"}