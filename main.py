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

from pydantic import BaseModel
from typing import List

from app.cad.trench import register_layers as reg_trench, draw_trench_front, draw_trench_top
from app.cad.pipe import draw_pipe_front, register_layers as reg_pipe
from app.cad.surface import draw_surface_top, register_layers as reg_surface
from app.cad.passages import register_layers as reg_pass, draw_pass_front

from app.services.lv_matcher import best_matches_batch, parse_aufmass
from app.invoices.builder import make_invoice

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
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"], 
    allow_headers=["*"], 
)

class MatchRequest(BaseModel):
    session_id: str

class InvoiceRequest(BaseModel):
    session_id: str
    mapping:   List[dict]

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
Du bist eine reine JSON-API und darfst ausschließlich gültiges JSON
(keine Kommentare, kein Markdown) zurückgeben.

ACHTUNG: Füge NICHT eigenmächtig Oberflächenbefestigung hinzu,
außer der Benutzer schreibt ausdrücklich "Oberflächenbefestigung",
"Gehwegplatten", "Mosaikpflaster", "Verbundpflaster" o. Ä.

────────────────────────────────────────────
GRUNDREGELN
────────────────────────────────────────────
● Alle Maße in **Metern** (Komma oder Punkt als Dezimaltrenn­zeichen).  
● „DN150“ → diameter = 0.15 usw.  
● Füge keine Oberflächen­befestigung hinzu, außer der Nutzer fordert
  es ausdrücklich (Stichwortliste: „Oberflächenbefestigung“, „Gehwegplatten“,
  „Mosaikpflaster“, „Verbundpflaster“).

────────────────────────────────────────────
REGELN ZU „trench_index“
────────────────────────────────────────────
• Nur Baugräben besitzen das Feld "trench_index" (1-basiert, fortlaufend).
• Rohr, Oberflächenbefestigung und Durchstich dürfen dieses Feld NIE haben.

Beispiel (korrekt)
  {{ "type":"Baugraben", "length":5, "trench_index":1 }}
  {{ "type":"Rohr",      "diameter":0.15 }}

────────────────────────────────────────────
SETZE trench_index NUR BEI BAUGRABEN
────────────────────────────────────────────
• Für Rohr, Oberflächenbefestigung & Durchstich dieses Feld
  grundsätzlich weglassen.
• Ordinalzahlen („zweiter Baugraben“ …) wirken nur auf Baugräben.
• Beispiel (RICHTIG)
    {{ "type":"Rohr", "diameter":0.15 }}          # kein Index
• Beispiel (FALSCH – wird verworfen)
    {{ "type":"Rohr", "diameter":0.15, "trench_index":2 }}

────────────────────────────────────────────
MEHRERE OBJEKTE IN EINEM SATZ
────────────────────────────────────────────
①  **Stückzahl +x*y*zm**  
    „Zeichne mir **drei Baugräben** mit **5x5x2 m**.“
    ⟶ drei identische Baugräben (L=5, B=5, T=2).

②  **Stückzahl + Liste von Maßen**  
    „… drei Baugräben **5x5x2 m, 10x5x2 m und 20x5x2 m**.“
    ⟶ genau drei Baugräben, jedes Paar Maße einmal verwenden.
    Zahl der Maße **muss** zur Stückzahl passen; wenn nicht,
    nimm so viele Maße wie genannt sind
    (fehlende Baugräben → letztes Maß wiederverwenden).

③  **Keine Stückzahl, aber mehrere Maße**  
    „… Baugraben **5x5x2 m und 8x6x2 m**.“
    ⟶ zwei Baugräben.

Falls Satz keine dieser Formen erfüllt → erzeuge **ein** Element
gemäß der üblichen Regeln.

────────────────────────────────────────────
STÜCKZAHL VS. ORDINALZAHL
────────────────────────────────────────────
• Eine ausgeschriebene oder numerische **Stückzahl** (drei / 3 / fünf / 5 …)
  gibt an, wie viele Objekte _insgesamt_ erzeugt werden.

• Eine **Ordinalzahl** („erster“, „zweiten“, „3.“, „fünften“ …)
  bezeichnet nur den Index **eines** einzelnen Baugrabens
  und ersetzt KEINE Stückzahl.
  → Wenn keine Stückzahl vorhanden ist, erzeuge exakt **1** Objekt.

────────────────────────────────────────────
VALIDIEREN & BEREINIGEN (Pflicht!)
────────────────────────────────────────────
Bevor Du die Antwort zurückgibst:

1. Durchlaufe ALLE Objekte.
2. Wenn type ≠ "Baugraben"  →  lösche vorhandenes "trench_index".
3. Gib erst danach das finale JSON zurück.

────────────────────────────────────────────
JSON-Schema
────────────────────────────────────────────
Wir arbeiten mit diesem JSON-Schema, wobei bei
* Oberflächenbefestigung  ⇒  material + offset Pflicht sind,
* Durchstich              ⇒  width Pflicht ist; offset & pattern optional.

{{
  "elements": [
    {{
      "type": "string",   # Baugraben | Rohr | Oberflächenbefestigung | Durchstich
      "trench_index": 0,           // ➜  **nur erlaubter Key bei type == "Baugraben"**
      "length": 0.0,
      "width":  0.0,
      "depth":  0.0,
      "diameter": 0.0,

      "material": "",
      "offset":   0.0,
      "pattern":  ""      # nur für Durchstich (Schraffur-Name)
    }}
  ],
  "answer": ""
}}

Aktuelles JSON:
{json.dumps(current_json, indent=2)}

────────────────────────────────────────────
AUFGABE
────────────────────────────────────────────
ACHTUNG:
- Alle Maße in Metern.
- „DN150“ o. Ä. wird als diameter = 0.15 erkannt.

• Lies "{description}".
• Erzeuge **genau so viele neue Objekte, wie die Beschreibung erfordert**.
  – Fehlt eine Stückzahl ⇒ 1 Objekt  
  – Stückzahl N ⇒ N Objekte  
  – Liste von Maßen ⇒ so viele Objekte wie Maß­paare  
• Verwende sequentialle trench_index-Werte,
  beginnend bei (höchster vorhandener Index + 1).

────────────────────────────────────────────
ANTWORTFORMAT  (genau so!)
────────────────────────────────────────────
{{
  "new_elements": [  ...NEUE Objekte...  ],
  "answer": "<max. 2 Sätze>"
}}
"""

    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo-0125",
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": "You are a JSON API."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=500,
            temperature=0.0,
        )
        raw_output = response.choices[0].message.content
        new_json = json.loads(raw_output)

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Fehler ChatGPT: {str(e)}")

    # Die neue JSON-Struktur (elements + answer) in der Session speichern
    added = new_json.get("new_elements") or new_json.get("elements") or []
    if not isinstance(added, list):
        raise HTTPException(400, "Antwort enthielt keine gültige Element-Liste.")

    # ② Session initialisieren (falls Nutzer direkt /add-element aufruft)
    session = session_data.setdefault(session_id, {"elements": []})

    # ③ Anhängen statt Überschreiben
    session["elements"].extend(added)

    # ④ optionale Antwort des Modells weitergeben
    answer_txt = new_json.get("answer", "")

    # Dann an den Client beides zurücksenden
    return {
      "status": "ok",
      "updated_json": session,
      "answer": answer_txt
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
        dxf_file_path, aufmass_str = _generate_dxf_intern(current_json)

        session_data[session_id]["elements"].append({
            "type": "aufmass",
            "text": aufmass_str
        })

        return FileResponse(
            dxf_file_path,
            media_type="application/dxf",
            filename=os.path.basename(dxf_file_path),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

def _generate_dxf_intern(parsed_json) -> tuple[str, str]:
    # ---------- DXF-Grundgerüst ----------
    doc = ezdxf.new("R2018", setup=True)
    msp = doc.modelspace()

    reg_trench(doc)
    reg_pipe(doc)
    reg_surface(doc)
    reg_pass(doc)

    doc.header["$LTSCALE"]  = 1.0
    doc.header["$CELTSCALE"] = 1.0
    doc.header["$PSLTSCALE"] = 0

    # ---------- Elemente vorsortieren ----------
    trenches, pipes, surfaces, passes = [], [], [], []
    for el in parsed_json.get("elements", []):
        t = el.get("type", "").lower()
        if "baugraben"           in t: trenches.append(el)
        elif "rohr"              in t: pipes.append(el)
        elif "oberflächenbefest" in t: surfaces.append(el)
        elif "durchstich" in t:          passes.append(el)

    if not trenches:
        raise HTTPException(400, "Kein Baugraben vorhanden – bitte zuerst /add-element benutzen.")

    # ---------- Layout-Konstanten ----------
    CLR_LR    = 0.20    # freier Rand links/rechts (Front)
    CLR_BOT   = 0.20    # freier Rand unten
    GAP_BG    = 1.50    # Abstand zwischen zwei Baugräben
    TOP_SHIFT = 1.50    # Abstand Draufsicht → Vorderansicht

    cursor_x = 0.0      # X-Versatz des nächsten Baugrabens
    aufmass  = []       # sammelt Aufmaß-Zeilen

    def draw_one_trench(msp, cx, L, B, T, pipe=None, surf=None):
        origin_front = (cx, 0.0)
        draw_trench_front(msp, origin_front, L, T,
                        clearance_left=CLR_LR, clearance_bottom=CLR_BOT)
        draw_trench_top(msp, (cx+CLR_LR, T+CLR_BOT+TOP_SHIFT),
                        length=L, width=B)
        _maybe_pipe(msp, [pipe] if pipe else [], 0, cx+CLR_LR, L, T)
        _maybe_surf(msp, [surf] if surf else [], 0, cx+CLR_LR, L, B, T)

    def _maybe_pipe(msp, pipes, idx, ox, L, T):
        if idx >= len(pipes) or not pipes[idx]:
            return
        d = float(pipes[idx].get("diameter", 0))
        if d:
            draw_pipe_front(msp, origin_front=(ox, CLR_BOT),
                            trench_inner_length=L, diameter=d)

    def _maybe_surf(msp, surfs, idx, ox, L, B, Tcombo):
        if idx >= len(surfs) or not surfs[idx]:
            return
        off = float(surfs[idx].get("offset", 0))
        if off:
            draw_surface_top(
                msp,
                trench_top_left=(ox, Tcombo+CLR_BOT+TOP_SHIFT),
                trench_length=L, trench_width=B,
                offset=off,
                material_text=f"Oberfläche: {surfs[idx].get('material','')}"
            )

    def add_aufmass(i, L, B, T, *, pipe, surf):
        aufmass.append(f"Baugraben {i+1}: l={L} m  b={B} m  t={T} m")
        if i < len(pipe) and pipe[i]:
            d = pipe[i].get("diameter", 0)
            if d:
                p_len = pipe[i].get("length", max(0, L - 1))
                aufmass.append(f"Rohr {i+1}: l={p_len} m  Ø={d} m")
        if i < len(surf) and surf[i]:
            off = surf[i].get("offset", 0)
            if off:
                aufmass.append(f"Oberfläche {i+1}: Randzone {off} m")

    def _add_surface_to_aufmass(idx: int, surf: dict):
        off = float(surf.get("offset", 0))
        if off:
            mat = surf.get("material", "")
            aufmass.append(f"Oberfläche {idx}: Randzone={off} m  Material={mat}")
    
    # ---------- Hauptschleife über alle Baugräben ----------
    i = 0
    while i < len(trenches):
        # --- Basisdaten des aktuellen (linken) Grabens ----
        bg1 = trenches[i]
        L1, B1, T1 = map(float, (bg1["length"], bg1["width"], bg1["depth"]))

        # ❶ Prüfen, ob es *direkt nach* diesem BG einen Durchstich gibt
        has_pass   = i < len(passes)          # gleicher Index
        merge_next = has_pass and (i+1 < len(trenches))

        # --------------------------------------------------
        # A) FALL - Kein Durchstich  →  wie bisher
        # --------------------------------------------------
        if not merge_next:
            # Vorder- und Draufsicht BG wie früher
            draw_trench_front(msp, (cursor_x, 0), L1, T1,
                            clearance_left=CLR_LR, clearance_bottom=CLR_BOT)
            draw_trench_top(msp, (cursor_x+CLR_LR, T1+CLR_BOT+TOP_SHIFT),
                            length=L1, width=B1)

            # optional Rohr
            if i < len(pipes):
                pipe = pipes[i]
                d = float(pipe.get("diameter", 0))
                if d:
                    draw_pipe_front(msp, origin_front=(cursor_x+CLR_LR, CLR_BOT),
                                    trench_inner_length=L1, diameter=d)
                    pipe_len = pipe.get("length", max(0, L1 - 1))
                    aufmass.append(f"Rohr {i+1}: l={pipe_len} m  Ø={d} m")

            # optional Oberfläche
            if i < len(surfaces):
                surf = surfaces[i]
                off  = float(surf.get("offset", 0))
                if off:
                    draw_surface_top(msp,
                        trench_top_left=(cursor_x+CLR_LR, T1+CLR_BOT+TOP_SHIFT),
                        trench_length=L1, trench_width=B1,
                        offset=off, material_text=f"Oberfläche: {surf.get('material','')}")
                    aufmass.append(f"Oberfläche {i+1}: Randzone={off} m  Material={surf.get('material','')}")

            aufmass.append(f"Baugraben {i+1}: l={L1} m  b={B1} m  t={T1} m")
            cursor_x += L1 + 2*CLR_LR + GAP_BG
            i += 1
            continue

        # --------------------------------------------------
        # B) FALL - Durchstich  →  BG1 + BG2 fusionieren
        # --------------------------------------------------
        bg2 = trenches[i+1]
        L2, B2, T2 = map(float, (bg2["length"], bg2["width"], bg2["depth"]))

        # Äußere Geometrie: Länge = L1+L2 (+ 2×CLR_LR), Tiefe = max(T1,T2)
        L_combo = L1 + L2
        T_combo = max(T1, T2)
        B_combo = max(B1, B2)

        # Vorder- + Draufsicht des *kombinierten* Baugrabens zeichnen
        origin_front = (cursor_x, 0.0)
        draw_trench_front(
            msp, origin_front, L_combo, T_combo,
            clearance_left=CLR_LR, clearance_bottom=CLR_BOT
        )

        # Durchstich platzieren -----------------------------------------
        pas    = passes[i]              # gleicher Index
        p_w    = float(pas["width"])
        p_off  = float(pas.get("offset", L1 - p_w/2))   # Default mittig „Naht“
        
        top_y = T_combo + CLR_BOT + TOP_SHIFT
        top_left_1 = (cursor_x + CLR_LR, top_y)
        top_left_2 = (cursor_x + CLR_LR + p_off + p_w, top_y)

        left_len  = max(0, p_off) 
        right_len = max(0, L_combo - (p_off + p_w))

        # ---------- (1) Oberflächen zeichnen ----------
        if left_len > 0 and i < len(surfaces) and surfaces[i]:
            surf = surfaces[i]
            off  = float(surf.get("offset", 0))
            if off:
                draw_surface_top(
                    msp,
                    trench_top_left=top_left_1,
                    trench_length=left_len,
                    trench_width=B1,
                    offset=off,
                    material_text=f"Oberfläche: {surf.get('material','')}"
                )
                _add_surface_to_aufmass(i+1, surfaces[i])

        if right_len > 0 and i+1 < len(surfaces) and surfaces[i+1]:
            surf = surfaces[i+1]
            off  = float(surf.get("offset", 0))
            if off:
                draw_surface_top(
                    msp,
                    trench_top_left=top_left_2,
                    trench_length=right_len,
                    trench_width=B2,
                    offset=off,
                    material_text=f"Oberfläche: {surf.get('material','')}"
                )
                _add_surface_to_aufmass(i+2, surfaces[i+1]) 

        # ------- linke Draufsicht zeichnen (falls >0) -------------
        if left_len:
            draw_trench_top(
                msp,
                top_left=(cursor_x + CLR_LR, top_y),
                length=left_len,
                width=B1
            )

        # ------- rechte Draufsicht zeichnen (falls >0) ------------
        if right_len:
            draw_trench_top(
                msp,
                top_left=(cursor_x + CLR_LR + p_off + p_w, top_y),
                length=right_len,
                width=B2
            )

        # ---------- Rohrdaten (links + rechts) ----------
        pipe_left  = pipes[i]   if i   < len(pipes) else None
        pipe_right = pipes[i+1] if i+1 < len(pipes) else None

        pipe_src = next((p for p in (pipe_left, pipe_right) if p and p.get("diameter")), None)
        if pipe_src:
            d = float(pipe_src["diameter"])
            # durchgehendes Rohr zeichnen
            draw_pipe_front(
                msp,
                origin_front=(cursor_x + CLR_LR, CLR_BOT),
                trench_inner_length=L_combo,
                diameter=d,
            )
            aufmass.append(f"Rohr {i+1}:  l={L_combo} m  Ø={d} m")

        draw_pass_front(
            msp,
            trench_origin=origin_front,
            trench_len=L_combo,
            trench_depth=T_combo,
            width=p_w,
            offset=p_off,
            clearance_left=CLR_LR,
            clearance_bottom=CLR_BOT,
            pattern=pas.get("pattern", "ANSI31"),
        )

        # Aufmaß ---------------------------------------------------------
        aufmass.append(f"Durchstich {i+1}: b={p_w} m  Versatz={p_off} m")
        aufmass.append(f"Baugraben {i+1}: l={left_len:.2f} m  b={B1} m  t={T1} m")
        aufmass.append(f"Baugraben {i+2}: l={right_len:.2f} m  b={B2} m  t={T2} m")

        # Cursor auf den Bereich *nach* BG2 setzen
        cursor_x += L_combo + 2*CLR_LR + GAP_BG
        i += 2        # zwei BGs auf einmal verarbeitet!

    # ---------- Aufmaß-Block als MText ----------
    msp.add_mtext(
        "Aufmaß:\n" + "\n".join(aufmass),
        dxfattribs={
            "layer": "Baugraben",
            "style": "ISOCPEUR",
            "char_height": 0.3,
        }
    ).set_location(insert=(0, -3.0), attachment_point=1)

    # ---------- Speichern ----------
    out_dir = "temp"
    os.makedirs(out_dir, exist_ok=True)
    file_path = os.path.join(out_dir, f"generated_{uuid.uuid4()}.dxf")
    doc.saveas(file_path)
    return file_path, "\n".join(aufmass)

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
Du bist eine JSON-API und darfst AUSSCHLIESSLICH gültiges JSON
zurückliefern, kein Fließtext.  Format siehe unten.

---------------------------------------------
ZIEL-ELEMENT FINDEN
---------------------------------------------
• Falls {instruction!r} eine Ordinalzahl enthält
  („ersten Baugraben“, „3. Baugraben“, „dritten Rohr“ …),
  gilt das als eindeutiger Index:
      erster/1.  →  trench_index = 1
      zweiter/2. →  2   …   sechster/6. → 6
• Enthält der Satz KEINE Ordinalzahl, wähle das
  **erste** Vorkommen des passenden Typs.

---------------------------------------------
ERLAUBTE FELDER FÜR EDIT
---------------------------------------------
length | width | depth | diameter | material | offset | pattern

Lass alle nicht genannten Felder UNVERÄNDERT!
Du darfst KEIN weiteres Element hinzufügen oder löschen.

---------------------------------------------
OUTPUT-FORMAT  (genau so!)
---------------------------------------------
{{
  "elements": [...alle Objekte, in Originalreihenfolge...],
  "answer": "max. 2 Sätze auf Deutsch"
}}

---------------------------------------------
AKTUELLES JSON
---------------------------------------------
{json.dumps(current_json, indent=2)}
---------------------------------------------
JETZT AUFGABE
---------------------------------------------
Ändere exakt *ein* Element gemäss:  {instruction!r}
"""

    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo-0125",
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": "You are a JSON API."},
                {"role": "user", "content": prompt},
            ],
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
Du bist eine JSON-API und darfst AUSSCHLIESSLICH gültiges JSON
ausgeben, kein Markdown oder sonstigen Text.

------------------------------------------------
SCHRITT 1 – Element bestimmen
------------------------------------------------
• Wenn {instruction!r} eine Ordinalzahl enthält
  (z. B. „2. Baugraben“, „dritten Rohr“, „vierter Durchstich“ …),
  wähle genau das Objekt mit diesem `trench_index`
  (Mapping: erster/1., zweiter/2., … sechster/6.).
• Andernfalls lösche das **erste** Objekt,
  dessen `type` zum genannten Begriff passt
  (Baugraben | Rohr | Oberflächenbefestigung | Durchstich).

------------------------------------------------
SCHRITT 2 – Löschen
------------------------------------------------
• Entferne **nur** dieses eine Element.
• Reihenfolge aller übrigen Objekte beibehalten.

------------------------------------------------
AUSGABEFORMAT (exakt so!)
------------------------------------------------
{{
  "elements": [... verbleibende Objekte ...],
  "answer": "max. 2 kurze Sätze auf Deutsch"
}}

------------------------------------------------
AKTUELLES JSON
------------------------------------------------
{json.dumps(current_json, indent=2)}
------------------------------------------------
AUFGABE
------------------------------------------------
Lösche das beschriebene Element jetzt.
"""


    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo-0125",
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": "You are a JSON API."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=500,
            temperature=0.0,
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

# -----------------------------------------------------
# Match LV with Aufmass
# -----------------------------------------------------
@app.post("/match-lv/")
async def match_lv(data: MatchRequest):
    sess = session_data.get(data.session_id) or {}
    aufmass_txt = next((e["text"] for e in sess.get("elements",[])
                        if e.get("type")=="aufmass"), "")
    lines = parse_aufmass(aufmass_txt)
    mapping = await best_matches_batch(lines)
    return {"mapping": mapping}

# -----------------------------------------------------
# Generate Invoice
# -----------------------------------------------------
@app.post("/invoice/")
def build_invoice(req: InvoiceRequest):
    file = f"temp/invoice_{uuid.uuid4()}.pdf"
    make_invoice(file, company="Muster GmbH", mapping=req.mapping)
    return FileResponse(file, media_type="application/pdf",
                        filename="Rechnung.pdf")
