# app/services/lv_loader.py
from __future__ import annotations
from functools import lru_cache
import json, os
from pathlib import Path
from typing import List, Dict, Any

DEFAULT_FILES = [
    "app/specifications/2_preiskatalog-strassenbauarbeiten.json",
    "app/specifications/3_preiskatalog-erdarbeiten.json",
    "app/specifications/5_preiskatalog-rohrleitungsarbeiten.json",
]

LV_FILES = [
    *filter(bool, os.getenv("LV_FILES", "").split(","))
] or DEFAULT_FILES

# ── NEW: Dateiname → Label (fallback = Dateiname)
def _label_for_file(p: Path) -> str:
    name = p.name.lower()
    if "strassenbau" in name or "straßenbau" in name:
        return "Straßenbauarbeiten"
    if "erdarbeit" in name:
        return "Erdarbeiten"
    if "rohrleitungs" in name or "rohrleitung" in name:
        return "Rohrleitungsarbeiten"
    return p.stem

def _normalize_item(it: Dict[str, Any], *, catalog: str) -> Dict[str, Any]:
    T1  = str(it.get("T1", "")).strip()
    T2  = str(it.get("T2", "")).strip()
    Pos = str(it.get("Pos","")).strip()
    out = {
        "T1": T1, "T2": T2, "Pos": Pos,
        "description": it.get("description") or it.get("Beschreibung") or it.get("text") or "",
        "price": it.get("price") or it.get("Einheitspreis") or None,
        "unit": it.get("unit") or it.get("Einheit") or None,
        "catalog": catalog,                              # ── NEW
    }
    out["code"] = f"{T1}.{T2}.{Pos}"
    return out

@lru_cache(maxsize=1)
def load_lv() -> List[Dict[str, Any]]:
    data: List[Dict[str, Any]] = []
    for f in LV_FILES:
        p = Path(f)
        if not p.exists():
            raise FileNotFoundError(f"LV-Datei fehlt: {p}")
        try:
            arr = json.loads(p.read_text(encoding="utf-8"))
            if not isinstance(arr, list):
                raise ValueError(f"Datei ist kein JSON-Array: {p}")
            label = _label_for_file(p)
            for x in arr:
                data.append(_normalize_item(x, catalog=label))
        except Exception as e:
            raise RuntimeError(f"Fehler beim Laden {p}: {e}")

    def _key(x):
        def to_int(s):
            try: return int(str(s))
            except: return 10**9
        return (x["catalog"], to_int(x["T1"]), to_int(x["T2"]), to_int(x["Pos"]))
    data.sort(key=_key)
    return data

# optional: Filter um 'catalog' zu unterstützen (bestehende Aufrufer bleiben kompatibel)
def search_lv(q: str | None = None, t1: str | None = None, t2: str | None = None,
              catalog: str | None = None) -> List[Dict[str, Any]]:
    items = load_lv()
    if catalog:
        items = [x for x in items if x["catalog"] == catalog]
    if t1:
        items = [x for x in items if str(x["T1"]) == str(t1)]
    if t2:
        items = [x for x in items if str(x["T2"]) == str(t2)]
    if q:
        ql = q.lower()
        items = [x for x in items if ql in x["description"].lower() or ql in x["code"].lower()]
    return items