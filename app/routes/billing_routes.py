from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List
from fastapi.responses import FileResponse
from dotenv import load_dotenv
from hashlib import sha1

import re
import os
import uuid, pathlib
import json

from app.utils.session_manager import session_manager
from app.services.lv_matcher     import best_matches_batch, parse_aufmass
from app.invoices.builder       import make_invoice
from app.services.lv_loader import load_lv

from openai import AsyncOpenAI

# Lädt automatisch die .env-Datei aus dem aktuellen Verzeichnis
load_dotenv()

# OpenAI-Key
async_client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

router = APIRouter()  

CONFIDENCE_THRESHOLD = 0.8

# ---------- Pydantic ----------
class MatchRequest(BaseModel):
    session_id: str

class InvoiceRequest(BaseModel):
    session_id: str
    mapping   : List[dict]

# ---------- GPT-Helfer ----------
async def extract_dims_gpt(line: str) -> dict:
    prompt = (
        "Extrahiere aus folgendem Aufmaßtext die Maße als JSON mit den "
        "Feldern L, B, T (in Meter, falls vorhanden):\n"
        f"{line}\n"
        "Antworte nur mit JSON, z.B. {\"L\": 5.0, \"B\": 1.0, \"T\": 2.0}"
    )
    resp = await async_client.chat.completions.create(
        model            = "gpt-4o-mini",
        temperature      = 0.0,
        response_format  = {"type": "json_object"},
        messages = [
            {"role": "system", "content": "Du bist ein Assistent für Bauaufmaße."},
            {"role": "user",   "content": prompt},
        ],
        max_tokens = 100,
    )
    return json.loads(resp.choices[0].message.content)

# ---------- /match-lv ----------
@router.post("/match-lv")
async def match_lv(req: MatchRequest):
    sess = session_manager.get_session(req.session_id)
    if not sess.get("elements"):
        raise HTTPException(404, "Session unknown oder empty")


    # Manuellen Override bevorzugen, sonst letzten Auto-Aufmaßblock
    manual = next((e["lines"] for e in reversed(sess["elements"])
                   if e.get("type") == "aufmass_override"), None)
    if manual:
        lines = [l for l in manual if l.strip()]
    else:
        texts = [e["text"] for e in sess["elements"] if e.get("type") == "aufmass"]
        if not texts:
            raise HTTPException(400, "Aufmaß fehlt – zuerst DXF erstellen")
        lines = parse_aufmass(texts[-1])

    # ---------- NEU: zuerst harte Links aus der Session anwenden ----------
    # (Hash über die exakte Aufmaßzeile → LV-Code)
    links = sess.get("lv_links", {})
    lv_items = load_lv()
    by_code  = {x["code"]: x for x in lv_items}

    hard_assigned: dict[int, dict] = {}
    for i, line in enumerate(lines):
        h = sha1(line.strip().encode("utf-8")).hexdigest()
        code = links.get(h)
        if code and code in by_code:
            hard_assigned[i] = by_code[code]

    # Nur die noch nicht verlinkten Zeilen von GPT matchen lassen
    to_match_idx = [i for i in range(len(lines)) if i not in hard_assigned]
    to_match     = [lines[i] for i in to_match_idx]
    results      = await best_matches_batch(to_match)
    res_by_idx   = {i: r for i, r in zip(to_match_idx, results)}
    # ----------------------------------------------------------------------

    assigned, to_review = [], []

    # Beim Zusammenbauen: für jede Zeile ggf. harten Link nutzen
    for idx, line in enumerate(lines):
        # Maße (L,B,T, qty) immer extrahieren – auch bei hartem Link
        dims = await extract_dims_gpt(line)
        base = {
            "aufmass":      line,
            "L":            dims.get("L", 0),
            "B":            dims.get("B", 0),
            "T":            dims.get("T", 0),
            "qty":          dims.get("L", 0),
            "confidence":   1.0,          # Default – wird ggf. überschrieben
            "alternatives": [],
        }

        if idx in hard_assigned:
            base["match"] = hard_assigned[idx]
            assigned.append(base)         # feste Verknüpfung → direkt „assigned“
            continue

        res = res_by_idx[idx]
        base["confidence"]   = res["confidence"]
        base["alternatives"] = res["alternatives"]

        if res["confidence"] >= CONFIDENCE_THRESHOLD:
            base["match"] = res["match"]
            assigned.append(base)
        else:
            to_review.append(base)

    return {
        "assigned":  assigned,
        "to_review": to_review
    }

# ---------- /invoice ----------
@router.post("/invoice")
def build_invoice(req: InvoiceRequest):
    pathlib.Path("temp").mkdir(exist_ok=True)
    pdf = f"temp/invoice_{uuid.uuid4()}.pdf"
    make_invoice(pdf, company="Muster GmbH", mapping=req.mapping)
    return FileResponse(pdf,
                        media_type="application/pdf",
                        filename="Rechnung.pdf")
