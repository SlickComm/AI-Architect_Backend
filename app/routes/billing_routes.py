from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List
from fastapi.responses import FileResponse
from dotenv import load_dotenv

import re
import os
import uuid, pathlib
import json

from app.utils.session_manager import session_manager
from app.services.lv_matcher     import best_matches_batch, parse_aufmass
from app.invoices.builder       import make_invoice

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
@router.post("/match-lv/")
async def match_lv(req: MatchRequest):
    sess = session_manager.get_session(req.session_id)
    if not sess.get("elements"):
        raise HTTPException(404, "Session unknown oder empty")

    # Alle Aufmaß-Texte sammeln und den letzten nehmen
    texts = [e["text"] for e in sess["elements"] if e.get("type") == "aufmass"]
    if not texts:
        raise HTTPException(400, "Aufmaß fehlt – zuerst DXF erstellen")
    aufmass_txt = texts[-1]

    # Zeilen extrahieren & Matching durchführen
    lines   = parse_aufmass(aufmass_txt)
    results = await best_matches_batch(lines)

    if len(lines) != len(results):
        raise HTTPException(
            500,
            f"Mismatch: {len(lines)} Aufmaßzeilen vs. {len(results)} GPT-Treffer"
        )

    assigned  = []
    to_review = []

    for line, res in zip(lines, results):
        dims = await extract_dims_gpt(line)
        base = {
            "aufmass":      line,
            "L":            dims.get("L", 0),
            "B":            dims.get("B", 0),
            "T":            dims.get("T", 0),
            "qty":          dims.get("L", 0),
            "confidence":   res["confidence"],
            "alternatives": res["alternatives"],
        }
        if res["confidence"] >= CONFIDENCE_THRESHOLD:
            # sichere Matches
            base["match"] = res["match"]
            assigned.append(base)
        else:
            # unsichere → nur Vorschläge
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
