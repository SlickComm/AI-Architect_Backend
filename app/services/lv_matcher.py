import os
import json, asyncio, re

from pathlib import Path
from typing import List, Dict, Any
from dotenv import load_dotenv

from openai import AsyncOpenAI

from app.services.lv_loader import load_lv

# Lädt automatisch die .env-Datei aus dem aktuellen Verzeichnis
load_dotenv()

# OpenAI-Key
async_client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

CATALOG: List[Dict[str, Any]] = load_lv()

# -----------------  simple Vorfilter  ------------------
def _rough_filter(line: str) -> List[Dict[str, Any]]:
    """
    Verkleinerter Ausschnitt (<~150 Einträge) basierend auf Volltext in Beschreibung/Code.
    """
    kw = line.lower()
    def score(p: Dict[str, Any]) -> int:
        s = (p.get("description") or "").lower()
        c = (p.get("code") or "").lower()
        sc = 0
        for token in ("rohr","graben","oberfläche","pflaster","gehweg","bord","fuge","beton","asphalt"):
            if token in s: sc += 2
        if any(tok in s for tok in kw.split()): sc += 1
        if any(tok in c for tok in kw.split()): sc += 1
        return sc

    ranked = sorted(CATALOG, key=score, reverse=True)
    head = [p for p in ranked if score(p) > 0][:150]    
    return head if head else ranked[:150]

# ------------------  GPT-Matching  ----------------------
SYSTEM_PROMPT = """\
Du bist eine Ausschreibungs-KI.
Suche zu jedem Aufmaßtext die best­passende LV-Position aus der mitgelieferten Liste.

Antworte **nur** mit gültigem JSON:

{
  "match":        { ... genau der richtige LV-Eintrag ... },
  "confidence":   0-1,               // geschätzte Treffer­qualität
  "alternatives": [ {...}, {...} ]   // max. 2 weitere Kandidaten
}
"""

async def _match_line(line: str) -> Dict[str, Any]:
    cat = _rough_filter(line)
    user_prompt = (
        f"Aufmaßzeile:\n{line}\n\n"
        f"LV-Auszug (JSON-Liste):\n{json.dumps(cat, ensure_ascii=False)}"
    )

    resp = await async_client.chat.completions.create(   # <── jetzt async OK
        model="gpt-4o-mini",
        temperature=0.0,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        max_tokens=500,
    )
    
    # ---- DEBUG -------------------------------------------------
    print("Aufmaßzeile:", line)
    
    result = json.loads(resp.choices[0].message.content)
    print("▶︎ GPT-Ergebnis:", json.dumps(result, indent=2, ensure_ascii=False))
    # ------------------------------------------------------------

    return json.loads(resp.choices[0].message.content)

async def best_matches_batch(lines: list[str]) -> list[dict]:
    """mehrere Aufmaßzeilen parallel – 100 % async"""
    return await asyncio.gather(*(_match_line(l) for l in lines))

def best_matches_batch_sync(lines: List[str]) -> List[Dict[str, Any]]:
    import anyio
    return anyio.from_thread.run(asyncio.run, best_matches_batch(lines))

# -------- Aufmaß-Block parsen (wie gehabt) -------------
_RX = re.compile(r":\s*(.*)$")
def parse_aufmass(block: str) -> List[str]:
    return [_RX.search(l).group(1) for l in block.splitlines() if ":" in l]