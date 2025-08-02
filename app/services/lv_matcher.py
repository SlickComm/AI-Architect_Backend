import os
from dotenv import load_dotenv
import json, asyncio, re

from pathlib import Path
from typing import List, Dict, Any

import asyncio

from openai import AsyncOpenAI

# Lädt automatisch die .env-Datei aus dem aktuellen Verzeichnis
load_dotenv()

# OpenAI-Key
async_client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# -------------------  Katalog laden  -------------------
CATALOG_FILES = [
    "app/specifications/2_preiskatalog-strassenbauarbeiten.json",
    "app/specifications/3_preiskatalog-erdarbeiten.json",
    "app/specifications/5_preiskatalog-rohrleitungsarbeiten.json",
]

CATALOG: List[Dict[str, Any]] = []
for f in CATALOG_FILES:
    CATALOG += json.loads(Path(f).read_text(encoding="utf-8"))

# -----------------  simple Vorfilter  ------------------
def _rough_filter(line: str) -> List[Dict[str, Any]]:
    """
    Gibt einen verkleinerten Katalog-Ausschnitt (< ~120 Positionen) zurück,
    damit der Prompt im 16k-Kontext bleibt und Kosten spart.
    Das Heuristik-Matching kannst du nach Belieben verfeinern.
    """
    kw = line.lower()

    def ok(p):
        cat = p.get("category", "").lower()
        return (
            any(w in cat for w in ("rohr", "graben", "leitungs"))
            or str(p.get("dn", "")).lower() in kw
            or str(p.get("rohrgrabentiefe_m", "")).lower() in kw
        )

    subset = [p for p in CATALOG if ok(p)]
    return subset[:120] if subset else CATALOG[:120]


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
    print("\n▶︎ Aufmaß:", line)
    
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