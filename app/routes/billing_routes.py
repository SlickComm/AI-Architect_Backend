from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List
from fastapi.responses import FileResponse

import re
import uuid, pathlib

from app.utils.session_manager import session_data
from app.services.lv_matcher import best_matches_batch, parse_aufmass
from app.invoices.builder import make_invoice

router = APIRouter()

# --- Pydantic ---
class MatchRequest(BaseModel):
    session_id: str

class InvoiceRequest(BaseModel):
    session_id: str
    mapping:   List[dict]

# -------- /match-lv ----------
@router.post("/match-lv/")
async def match_lv(req: MatchRequest):
    sess = session_data.get(req.session_id) or {}
    aufmass_txt = next((e["text"] for e in sess.get("elements", [])
                        if e.get("type") == "aufmass"), "")
    if not aufmass_txt:
        raise HTTPException(400, "Aufmaß fehlt – zuerst DXF erstellen")

    lines   = parse_aufmass(aufmass_txt)          # ['l=5m b=1m t=2m', …]
    results = await best_matches_batch(lines)      # GPT-Treffer

    def _dims(line: str):
        m = re.search(r"([\d.,]+)\s*[x×]\s*([\d.,]+)\s*[x×]\s*([\d.,]+)", line)
        if m:                                         # 5x1x2-Schreibweise
            return map(lambda s: float(s.replace(",", ".")), m.groups())

        def _pick(rx):                               # l= … b= … t= …
            m2 = re.search(rx, line, flags=re.I)
            return float(m2.group(1).replace(",", ".")) if m2 else 0.0

        return _pick(r"l\s*=\s*([\d.,]+)"), \
               _pick(r"b\s*=\s*([\d.,]+)"), \
               _pick(r"t\s*=\s*([\d.,]+)")

    mapping = []
    for line, res in zip(lines, results):
        L, B, T = _dims(line)
        print("🔍", line, "→", (L, B, T))  
        mapping.append({
            "aufmass" : line,
            "L"       : L,
            "B"       : B,
            "T"       : T,
            "qty"     : L,              # Multiplikator = Länge
            "match"   : res["match"],
            "alternatives": res["alternatives"],
            "confidence"  : res["confidence"],
        })
    return {"mapping": mapping}

print("🔍", line, "→", _parse_dims(line))

# -------- /invoice ----------
@router.post("/invoice/")
def build_invoice(req: InvoiceRequest):
    pathlib.Path("temp").mkdir(exist_ok=True)
    pdf = f"temp/invoice_{uuid.uuid4()}.pdf"
    make_invoice(pdf, company="Muster GmbH", mapping=req.mapping)
    return FileResponse(pdf,
                        media_type="application/pdf",
                        filename="Rechnung.pdf")
