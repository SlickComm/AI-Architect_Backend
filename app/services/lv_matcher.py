import os
import math
import json, asyncio, re

from pathlib import Path
from typing import List, Dict, Any
from dotenv import load_dotenv

from openai import AsyncOpenAI

from app.services.lv_loader import load_lv

from langsmith.wrappers import wrap_openai

# Lädt automatisch die .env-Datei aus dem aktuellen Verzeichnis
load_dotenv()

# Langsmith-key
os.environ["LANGSMITH_TRACING"] = "true"
os.environ["LANGSMITH_ENDPOINT"] = "https://api.smith.langchain.com"
os.getenv("LANGSMITH_API_KEY")
os.getenv("LANGSMITH_PROJECT")
os.environ["LANGSMITH_DEBUG"] = "true"

# OpenAI-Key
async_client = wrap_openai(AsyncOpenAI(
    api_key=os.getenv("OPENAI_API_KEY"),
    base_url="https://openrouter.ai/api/v1"
))

CATALOG: List[Dict[str, Any]] = load_lv()

# -----------------  simple Vorfilter  ------------------
def _rough_filter(line: str, *, dims: Dict[str, Any] | None = None, kind: str | None = None) -> List[Dict[str, Any]]:
    """
    Deterministic preselection:
      - For 'baugraben': use aushubbreite + rohrgrabentiefe_m from Erdarbeiten.
      - Otherwise: old keyword-based shortlist.
    """
    kind = kind or _classify_line(line)
    dims = dims or {}
    if kind == "baugraben":
        b = _to_float(dims.get("B"))
        t = _to_float(dims.get("T"))
        cand = _trench_candidates(b, t)
        return cand if cand else CATALOG[:150]

    # --- fallback (previous behaviour) ---
    kw = line.lower()
    def score(p: Dict[str, Any]) -> int:
        s = (p.get("description") or "").lower()
        c = (p.get("code") or "").lower()
        sc = 0
        for token in ("rohr","graben","oberfläche","pflaster","gehweg","bord","fuge","beton","asphalt","durchstich"):
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

Regeln (wichtig):
1) Wenn die Aufmaßzeile ein *Baugraben* ist, wähle die Position **deterministisch**:
   • Nur Katalog „Erdarbeiten“ (3_preiskatalog-erdarbeiten.json).
   • Kategorie „Rohrgraben“.
   • Breite B muss in die Spanne `aushubbreite` fallen (min ≤ B ≤ max).
   • Tiefe T: wähle die Position mit `rohrgrabentiefe_m` >= T; bei mehreren die mit
     dem kleinsten Abstand zu T.
   • Wenn mehrere gleich gut: nimm die erste in der Kandidatenliste.
2) Für *Rohr* (Druckrohr/Leitung) und *Durchstich* nutze Semantik der Zeile
   sowie die mitgelieferte Kandidatenliste.
3) Antworte **nur** mit gültigem JSON:

{
  "match":        { ... der beste LV-Eintrag ... },
  "confidence":   0-1,
  "alternatives": [ {...}, {...} ]
}
"""

async def _match_line(line: str, hint: Dict[str, Any] | None = None) -> Dict[str, Any]:
    hint = hint or {}
    dims = hint.get("dims") or {}
    kind = hint.get("kind") or _classify_line(line)

    cat = _rough_filter(line, dims=dims, kind=kind)

    user_prompt = (
        f"Aufmaßzeile:\n{line}\n\n"
        f"Hinweise (JSON): {json.dumps({'kind': kind, 'dims': dims}, ensure_ascii=False)}\n\n"
        f"LV-Auszug (JSON-Liste):\n{json.dumps(cat, ensure_ascii=False)}"
    )

    resp = await async_client.chat.completions.create(
        model="openai/gpt-4o-mini",
        temperature=0.0,
        response_format={"type": "json_object"},
        messages=[{"role": "system", "content": SYSTEM_PROMPT},
                  {"role": "user", "content": user_prompt}],
        max_tokens=500,
    )
    
    # ---- DEBUG -------------------------------------------------
    print("Aufmaßzeile:", line)
    
    result = json.loads(resp.choices[0].message.content)
    print("▶︎ GPT-Ergebnis:", json.dumps(result, indent=2, ensure_ascii=False))
    # ------------------------------------------------------------

    return json.loads(resp.choices[0].message.content)

async def best_matches_batch(lines: list[str], hints: list[dict] | None = None) -> list[dict]:
    hints = hints or [{} for _ in lines]
    return await asyncio.gather(*(_match_line(l, h) for l, h in zip(lines, hints)))

def best_matches_batch_sync(lines: List[str], hints: List[dict] | None = None) -> List[Dict[str, Any]]:
    import anyio
    return anyio.from_thread.run(asyncio.run, best_matches_batch(lines, hints))

# -------- Aufmaß-Block parsen (wie gehabt) -------------
_RX = re.compile(r":\s*(.*)$")
def parse_aufmass(block: str) -> List[str]:
    return [_RX.search(l).group(1) for l in block.splitlines() if ":" in l]

def _to_float(s: str | float | int | None) -> float | None:
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    s = str(s).replace(" ", "").replace(",", ".")
    m = re.search(r"([0-9]+(?:\.[0-9]+)?)", s)
    return float(m.group(1)) if m else None

def _parse_aushubbreite_range(s: str | None) -> tuple[float | None, float | None]:
    """
    Parses strings like:
      '1,34 m < B ≤ 1,46 m'
      'B ≤ 1,00 m'
      '1,00 m ≤ B < 1,12 m'
    Returns (min_inclusive, max_inclusive) where None means open.
    """
    if not s:
        return (None, None)
    txt = s.replace(" ", " ").replace(",", ".")
    nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", txt)]
    # Heuristics
    if "≤" in txt or "<=" in txt or "<" in txt:
        if len(nums) == 2:
            lo, hi = nums[0], nums[1]
            return (lo, hi)
        if len(nums) == 1:
            # B ≤ X  or  X ≥ B
            if re.search(r"[≤<]\s*B", txt) or re.search(r"B\s*[≥>]", txt):
                return (nums[0], None)
            else:
                return (None, nums[0])
    if len(nums) == 2:
        return (min(nums), max(nums))
    if len(nums) == 1:
        return (None, nums[0])
    return (None, None)

def _classify_line(line: str) -> str:
    l = line.lower()
    if "baugraben" in l or "graben" in l:
        return "baugraben"
    if "rohr" in l or "druckrohr" in l or "leitung" in l:
        return "rohr"
    if "durchstich" in l or "bohrung" in l:
        return "durchstich"
    if "oberfl" in l or "pflaster" in l or "gehweg" in l:
        return "oberflaeche"
    return "sonst"

def _trench_candidates(b: float | None, t: float | None) -> list[Dict[str, Any]]:
    """
    Returns catalog items from 'Erdarbeiten' (Rohrgraben) whose aushubbreite range
    contains B and with rohrgrabentiefe_m >= T (closest).
    """
    pool = [x for x in CATALOG
            if x.get("catalog") == "Erdarbeiten" and (x.get("category") or "").lower().startswith("rohrgraben")]
    if b is None and t is None:
        return pool[:150]
    # filter by width bucket
    def width_ok(x) -> bool:
        lo, hi = _parse_aushubbreite_range(x.get("aushubbreite"))
        if b is None:
            return True
        if lo is not None and b < lo - 1e-9:  # strictly outside
            return False
        if hi is not None and b > hi + 1e-9:
            return False
        return True
    cand = [x for x in pool if width_ok(x)]
    # order by depth (first >= T, then nearest)
    def depth_rank(x):
        xdepth = _to_float(x.get("rohrgrabentiefe_m")) or math.inf
        if t is None:
            return (0, abs(xdepth))  # arbitrary but stable
        return (0 if xdepth >= t - 1e-9 else 1, abs(xdepth - t), xdepth)
    cand.sort(key=depth_rank)
    return cand[:150]