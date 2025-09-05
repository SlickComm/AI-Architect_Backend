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

from openai import AsyncOpenAI
from reportlab.lib import colors

from app.utils.session_manager import session_manager
from app.services.lv_matcher     import best_matches_batch, parse_aufmass
from app.invoices.builder       import make_invoice
from app.services.lv_loader import load_lv
from app.services.lv_matcher import best_matches_batch, parse_aufmass, _classify_line

from langsmith.wrappers import wrap_openai

# Lädt automatisch die .env-Datei aus dem aktuellen Verzeichnis
load_dotenv()

# Langsmith-key
os.environ["LANGSMITH_TRACING"] = True
os.environ["LANGSMITH_ENDPOINT"] = "https://api.smith.langchain.com"
os.getenv("LANGSMITH_API_KEY")
os.getenv("LANGSMITH_PROJECT")
os.environ["LANGSMITH_DEBUG"] = "true"


# OpenAI-Key
async_client = wrap_openai(AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY")))

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
        lines_full = [l for l in manual if l.strip()]          # volle Zeilen
    else:
        texts = [e["text"] for e in sess["elements"] if e.get("type") == "aufmass"]
        if not texts:
            raise HTTPException(400, "Aufmaß fehlt – zuerst DXF erstellen")
        lines_full = _split_full_lines(texts[-1])              # volle Zeilen

    # für Backward-Compat: Hash-Key = Teil NACH dem Doppelpunkt
    lines_key = [_after_colon(l) for l in lines_full]

    # --- harte Links (weiterhin über den "key")
    links = sess.get("lv_links", {})
    lv_items = load_lv()
    by_code  = {x["code"]: x for x in lv_items}
    hard_assigned: dict[int, dict] = {}
    for i, key in enumerate(lines_key):
        h = sha1(key.strip().encode("utf-8")).hexdigest()
        code = links.get(h)
        if code and code in by_code:
            hard_assigned[i] = by_code[code]

    # --- Hints (aus der VOLLEN Zeile! bessere Klassifizierung)
    all_hints, all_dims = [], []
    for line_full in lines_full:
        d = await extract_dims_gpt(line_full)  # mehr Kontext → robuster
        all_dims.append(d)
        all_hints.append({
            "kind": _classify_line(line_full),             # sieht "Baugraben", "Durchstich", …
            "dims": {"L": d.get("L"), "B": d.get("B"), "T": d.get("T")},
        })

    # Nur nicht-verlinkte matchen – aber mit der VOLLEN Zeile!
    to_match_idx = [i for i in range(len(lines_full)) if i not in hard_assigned]
    to_match     = [lines_full[i] for i in to_match_idx]      # <— wichtig
    to_hints     = [all_hints[i]   for i in to_match_idx]
    results      = await best_matches_batch(to_match, to_hints)
    res_by_idx   = {i: r for i, r in zip(to_match_idx, results)}

    assigned, to_review = [], []
    for idx, line_full in enumerate(lines_full):
        dims = all_dims[idx]
        base = {
            "aufmass":    line_full,        # <— jetzt voller Text
            "L":          dims.get("L", 0),
            "B":          dims.get("B", 0),
            "T":          dims.get("T", 0),
            "qty":        dims.get("L", 0),
            "confidence": 1.0,
            "alternatives": [],
        }

        if idx in hard_assigned:
            base["match"] = hard_assigned[idx]
            assigned.append(base)
            continue

        res = res_by_idx[idx]
        base["confidence"]   = res["confidence"]
        base["alternatives"] = res["alternatives"]
        if res["confidence"] >= CONFIDENCE_THRESHOLD:
            base["match"] = res["match"]
            assigned.append(base)
        else:
            to_review.append(base)

    return {"assigned": assigned, "to_review": to_review}

# ---------- /invoice ----------
@router.post("/invoice")
def build_invoice(req: InvoiceRequest):
    pathlib.Path("temp").mkdir(exist_ok=True)
    pdf = f"temp/invoice_{uuid.uuid4()}.pdf"
    
    make_invoice(
        file=pdf,                                
        company="BUG VERKEHRSBAU SE",
        mapping=req.mapping,                     
        recipient={"name": "DB InfraGO AG - BK 16 RB Ost",
                   "lines": ["Elisabeth-Schwarzhaupt-Platz 1", "10115 Berlin"]},
        invoice_meta={"nr": "AT0218-2025", "date": "18.02.2025",
                     "project": "TWL Kaulsdorf – techn. Bearbeitung"},
        cover_meta={"period":"Februar 2025",
                    "subject":"Bestell-Nr.: 0016/368/13503927 v. 03.01.2025",
                    "cost_center":"7201941",
                    "due":"21 Tage 3% Skonto, 30 Tage ohne Abzug"},
        logo_path=None,                         
        brand_color=colors.HexColor("#6DB33F"),
        add_cover=True
    )

    return FileResponse(pdf,
                        media_type="application/pdf",
                        filename="Rechnung.pdf")

# --- helper: split full lines & derive a "key" without prefix --------------
def _split_full_lines(block: str) -> list[str]:
    return [ln.strip() for ln in block.replace("\r","\n").split("\n")
            if ln.strip() and not ln.strip().lower().startswith("aufmaß")]

def _after_colon(s: str) -> str:
    m = re.search(r":\s*(.*)$", s)
    return m.group(1) if m else s