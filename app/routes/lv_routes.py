from __future__ import annotations
from collections import defaultdict
from typing import Optional, List, Dict, Any

from fastapi import APIRouter, Query, HTTPException         
from pydantic import BaseModel                              
from hashlib import sha1                                    

from app.utils.session_manager import session_manager
from app.services.lv_loader import search_lv, load_lv

router = APIRouter()

class LVLinkRequest(BaseModel):
    session_id: str
    line: str
    code: str 

def _as_row(it: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "key": it["code"],
        "code": it["code"],
        "T1": it["T1"], "T2": it["T2"], "Pos": it["Pos"],
        "description": it["description"],
        "price": it["price"],
        "unit": it["unit"],
        "catalog": it["catalog"],
    }

@router.get("/lv")
def get_lv(
    q: Optional[str] = Query(default=None, description="Volltextsuche"),
    t1: Optional[str] = None,
    t2: Optional[str] = None,
    format: str = Query(default="tabs", pattern="^(tabs|flat|catalogs)$"),
):
    items = search_lv(q, t1, t2)

    if format == "flat":
        return {"rows": [_as_row(x) for x in items]}

    if format == "catalogs":
        order = ["Straßenbauarbeiten", "Erdarbeiten", "Rohrleitungsarbeiten"]
        groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for it in items:
            groups[it["catalog"]].append(it)

        tabs = []
        for cat in order:
            rows = groups.get(cat, [])
            rows_sorted = sorted(rows, key=lambda r: (str(r["T1"]), str(r["T2"]), str(r["Pos"])))
            tabs.append({
                "key": cat.lower(),
                "title": cat,
                "rows": [_as_row(x) for x in rows_sorted],
            })
        # ggf. übrige, unbekannte Kataloge anhängen
        for cat in sorted(k for k in groups.keys() if k not in order):
            rows_sorted = sorted(groups[cat], key=lambda r: (str(r["T1"]), str(r["T2"]), str(r["Pos"])))
            tabs.append({
                "key": cat.lower(),
                "title": cat,
                "rows": [_as_row(x) for x in rows_sorted],
            })
        return {"tabs": tabs}

    # default: altes "tabs"-Verhalten (nach T1/T2 gruppiert)
    groups_tt: Dict[tuple[str,str], List[Dict[str, Any]]] = defaultdict(list)
    for it in items:
        groups_tt[(str(it["T1"]), str(it["T2"]))].append(it)

    tabs = []
    for (T1, T2), rows in sorted(groups_tt.items()):
        rows_sorted = sorted(rows, key=lambda r: r["Pos"])
        tabs.append({
            "key": f"{T1}-{T2}",
            "title": f"{T1}.{T2}",
            "rows": [_as_row(x) for x in rows_sorted],
        })
    return {"tabs": tabs}

@router.post("/lv-link")
def set_lv_link(req: LVLinkRequest):
    sess = session_manager.get_session(req.session_id)
    if not sess:
        raise HTTPException(404, "Session unknown")
    items = load_lv()
    item  = next((x for x in items if x["code"] == req.code), None)
    if not item:
        raise HTTPException(404, f"Code nicht gefunden: {req.code}")

    h = sha1(req.line.strip().encode("utf-8")).hexdigest()
    links = sess.setdefault("lv_links", {})
    links[h] = req.code
    session_manager.update_session(req.session_id, sess)
    return {"status": "ok", "code": req.code}