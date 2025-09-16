from __future__ import annotations

from fastapi import FastAPI, Body, HTTPException  
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from ezdxf.enums import const

import ezdxf
import os
import uuid
import json
import re

from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel
from typing import List, Optional

from app.cad.trench import register_layers as reg_trench, draw_trench_front, draw_trench_top, draw_trench_front_lr, LAYER_TRENCH_OUT, LAYER_TRENCH_IN, LAYER_HATCH, HATCH_PATTERN, HATCH_SCALE, DIM_OFFSET_FRONT, DIM_TXT_H, DIM_EXE_OFF
from app.cad.pipe import draw_pipe_front, draw_pipe_front_piecewise, register_layers as reg_pipe
from app.cad.surface import draw_surface_top_segments, draw_surface_top, register_layers as reg_surface
from app.cad.passages import register_layers as reg_pass, draw_pass_front

from app.services.lv_matcher import best_matches_batch, parse_aufmass
from app.invoices.builder import make_invoice
from app.routes import billing_routes
from app.routes import lv_routes
from app.routes import stripe_routes

from app.utils.session_manager import session_manager

from langsmith.wrappers import wrap_openai

app = FastAPI()

# Lädt automatisch die .env-Datei aus dem aktuellen Verzeichnis
load_dotenv()

# Langsmith-key
os.environ["LANGSMITH_TRACING"] = "true"
os.environ["LANGSMITH_ENDPOINT"] = "https://api.smith.langchain.com"
os.getenv("LANGSMITH_API_KEY")
os.getenv("LANGSMITH_PROJECT")
os.environ["LANGSMITH_DEBUG"] = "true"

# OpenAI-Key
client = wrap_openai(OpenAI(
    api_key=os.getenv("OPENAI_API_KEY"),
    base_url="https://openrouter.ai/api/v1",
))

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

class AufmassLinesRequest(BaseModel):
    session_id: str
    lines: List[str]

class InvoiceRequest(BaseModel):
    session_id: str
    mapping:   List[dict]

app.include_router(billing_routes.router, tags=["Billing"])
app.include_router(lv_routes.router, tags=["LV"])
app.include_router(stripe_routes.router, tags=["Payment"])

# -----------------------------------------------------
# START HELPER ADD-MODE
# -----------------------------------------------------
def _surfaces_for_trench(all_surfaces, trench_idx_1based: int):
    # keep original order; if "seq" is set, sort by it
    lst = [s for s in all_surfaces if int(s.get("for_trench", 0)) == trench_idx_1based]
    if any("seq" in s for s in lst):
        lst = sorted(lst, key=lambda s: int(s.get("seq", 1)))
    return lst

def _normalize_and_reindex(session: dict) -> None:
    elems = session.setdefault("elements", [])
    def tnorm(e): return e.get("type", "").lower()

    # ... Baugräben reindizieren wie gehabt ...
    trenches = [e for e in elems if "baugraben" in tnorm(e)]
    old_idx = [int(e.get("trench_index", i+1)) for i, e in enumerate(trenches)]
    idx_map = {}
    for new_i, (bg, old_i) in enumerate(zip(trenches, old_idx), start=1):
        idx_map[old_i] = new_i
        bg["trench_index"] = new_i
    N = len(trenches)

    keep = []
    pass_buffer = []   # Durchstiche ohne 'between' sammeln
    join_buffer = []   # Verbindungen ohne 'between' (z. B. group) sammeln

    for e in elems:
        tt = tnorm(e)

        if "baugraben" in tt:
            # GOK normalisieren
            if "gok" not in e or e["gok"] in (None, ""):
                e["gok"] = 0.0
            else:
                mv = _num_to_meters(e["gok"])
                e["gok"] = mv if mv is not None else 0.0
        else:
            # nur Nicht-Baugräben bereinigen
            e.pop("trench_index", None)

        if "rohr" in tt or "oberflächenbefest" in tt or "oberflaechenbefest" in tt:
            ref = int(e.get("for_trench", 0))
            if ref in idx_map:
                e["for_trench"] = idx_map[ref]
                keep.append(e)
            elif ref == 0 and N > 0:
                e["for_trench"] = N
                keep.append(e)
            # sonst verwerfen
        elif "durchstich" in tt:
            b = e.get("between")
            if b is not None:
                b = int(b or 0)
                if 1 <= b < N:
                    e["between"] = b
                    keep.append(e)
                # sonst verwerfen
            else:
                # später nummerieren
                pass_buffer.append(e)
        elif "verbindung" in tt:
            # bevorzugt 'between'; alternativ 'group' → expandieren
            if "between" in e and e["between"] is not None:
                b = int(e.get("between") or 0)
                if 1 <= b < N:
                    e["between"] = b
                    keep.append({"type":"Verbindung","between": b})
            elif isinstance(e.get("group"), (list, tuple)):
                # group → auf benachbarte Paare abbilden
                try:
                    g = [int(x) for x in e.get("group") if int(x) > 0]
                except Exception:
                    g = []
                # auf gültigen Bereich trimmen
                g = [x for x in g if 1 <= x <= N]
                # consecutive pairs: (g[i], g[i+1]) → seam = min(...) wenn Nachbarn
                for a, b in zip(g, g[1:]):
                    if abs(a - b) == 1:
                        seam = min(a, b)
                        if 1 <= seam < N:
                            keep.append({"type":"Verbindung","between": seam})
            # sonst verwerfen
        else:
            keep.append(e)

    # NEU: fehlende 'between' nach Reihenfolge 1..N-1 setzen
    for k, e in enumerate(pass_buffer, start=1):
        if k <= max(0, N-1):
            e["between"] = k
            keep.append(e)
        # sonst verwerfen (mehr Durchstiche als Nahtstellen)

    # Oberflächen-seq wie gehabt ...
    surfaces = [e for e in keep if ("oberflächenbefest" in tnorm(e) or "oberflaechenbefest" in tnorm(e))]
    from collections import defaultdict
    buckets = defaultdict(list)
    for s in surfaces:
        buckets[int(s.get("for_trench", 0))].append(s)
    for lst in buckets.values():
        lst.sort(key=lambda s: int(s.get("seq", 10**9)))
        for k, s in enumerate(lst, start=1):
            s["seq"] = k

    # --- Max. 1 Durchstich pro Nahtstelle (zwischen N und N+1) behalten ---
    pass_idxs = [i for i, e in enumerate(keep) if "durchstich" in tnorm(e)]
    seen_between = set()
    # von hinten nach vorn: den letzten Eintrag je 'between' behalten
    for i in reversed(pass_idxs):
        b = int(keep[i].get("between", 0) or 0)
        if not (1 <= b < N):
            # Sicherheit: ungültige Nahtstellen verwerfen
            del keep[i]
            continue
        if b in seen_between:
            del keep[i]
        else:
            seen_between.add(b)

    # --- Verbindungen deduplizieren; wenn es einen Durchstich an derselben Naht gibt, hat dieser Vorrang ---
    join_idxs = [i for i, e in enumerate(keep) if "verbindung" in tnorm(e)]
    join_seen = set()
    pass_seams = {int(e.get("between", 0) or 0) for e in keep if "durchstich" in tnorm(e)}
    for i in reversed(join_idxs):
        b = int(keep[i].get("between", 0) or 0)
        if not (1 <= b < N):
            del keep[i]
            continue
        if b in pass_seams or b in join_seen:
            del keep[i]
        else:
            join_seen.add(b)

    session["elements"] = keep

def _pipes_for_trench(all_pipes, idx: int):
    return [p for p in all_pipes if int(p.get("for_trench", 0)) == idx]

def _first_pipe_for_trench(all_pipes, idx: int):
    lst = _pipes_for_trench(all_pipes, idx)
    return lst[0] if lst else None

def _pass_for_between(all_passes, idx: int) -> Optional[dict]:
    # bevorzugt neues Feld 'between', sonst Legacy-Fallback per Listenposition
    by_between = [p for p in all_passes if p.get("between") is not None]
    if by_between:
        for p in by_between:
            if int(p.get("between", 0)) == idx:
                return p
        return None
    # Legacy: gleicher Listenindex wie linker Graben
    return all_passes[idx-1] if 0 <= idx-1 < len(all_passes) else None

def _tnorm(e: dict) -> str:
    return e.get("type", "").lower()

def _find_target_index_by_selection(elems: list[dict], sel: dict) -> Optional[int]:
    """sel = {type, trench_index? | for_trench? | between?, seq?}"""
    t = (sel.get("type") or "").lower()

    def is_surface(x): 
        tx = _tnorm(x)
        return ("oberflächenbefest" in tx) or ("oberflaechenbefest" in tx)

    if "baugraben" in t:
        ti = int(sel.get("trench_index", 0))
        for i, e in enumerate(elems):
            if "baugraben" in _tnorm(e) and int(e.get("trench_index", 0)) == ti:
                return i
        return None

    if "rohr" in t:
        ft = int(sel.get("for_trench", 0))
        cand = [i for i, e in enumerate(elems) if ("rohr" in _tnorm(e)) and int(e.get("for_trench", 0)) == ft]
        return cand[0] if cand else None

    if is_surface({"type": sel.get("type", "")}):
        ft = int(sel.get("for_trench", 0))
        seq = sel.get("seq", None)
        cand = [(i, e) for i, e in enumerate(elems) if is_surface(e) and int(e.get("for_trench", 0)) == ft]
        if not cand:
            return None
        if seq is not None:
            for i, e in cand:
                if int(e.get("seq", 0) or 0) == int(seq):
                    return i
            return None
        # kein seq angegeben → erste Oberfläche dieses Grabens (nach seq geordnet, sonst Ordn.)
        cand.sort(key=lambda p: int(p[1].get("seq", 10**9)))
        return cand[0][0]

    if "durchstich" in t:
        # Bevorzugt 'between' (zwischen N und N+1)
        if "between" in sel and sel["between"] is not None:
            b = int(sel["between"])
            cand = [i for i, e in enumerate(elems) if ("durchstich" in _tnorm(e)) and int(e.get("between", -1)) == b]
            if cand:
                return cand[0]
        # Legacy: n-ter Durchstich in Dokumentreihenfolge (falls explizit ordinal adressiert)
        if "ordinal" in sel and sel["ordinal"] is not None:
            k = int(sel["ordinal"])
            idxs = [i for i, e in enumerate(elems) if "durchstich" in _tnorm(e)]
            if 1 <= k <= len(idxs):
                return idxs[k-1]
        # Fallback: erster vorhandener Durchstich
        idxs = [i for i, e in enumerate(elems) if "durchstich" in _tnorm(e)]
        return idxs[0] if idxs else None

    if "verbindung" in t:
        if "between" in sel and sel["between"] is not None:
            b = int(sel["between"])
            cand = [i for i, e in enumerate(elems)
                    if ("verbindung" in _tnorm(e)) and int(e.get("between", -1)) == b]
            if cand:
                return cand[0]
        # Fallback: erste Verbindung
        idxs = [i for i, e in enumerate(elems) if "verbindung" in _tnorm(e)]
        return idxs[0] if idxs else None

    return None

_ALLOWED_EDIT_FIELDS = {"length","width","depth","diameter","material","offset","pattern"}

def _apply_update(elem: dict, updates: dict) -> None:
    for k, v in updates.items():
        if k in _ALLOWED_EDIT_FIELDS:
            elem[k] = v

def _sort_aufmass_lines(lines: list[str]) -> list[str]:
    """Sortiert Aufmaßzeilen nach:
       1) Baugraben, 2) Rohr(e), 3) Durchstich, 4) Oberfläche(n)
       und innerhalb nach natürlicher Nummerierung.
    """
    def key(line: str, pos: int):
        s = line.strip()

        m = re.match(r"^Baugraben\s+(\d+)\b", s, re.I)
        if m:  # Gruppe 0
            return (0, int(m.group(1)), 0, pos)

        # "Rohr 2:" oder "Rohr 1–3:" → nach erster Zahl sortieren
        m = re.match(r"^Rohr\s+(\d+)(?:\s*[–-]\s*(\d+))?", s, re.I)
        if m:  # Gruppe 1
            return (1, int(m.group(1)), 0, pos)

        m = re.match(r"^Durchstich\s+(\d+)\b", s, re.I)
        if m:  # Gruppe 2
            return (2, int(m.group(1)), 0, pos)

        # "Oberfläche 2:" oder "Oberfläche 2.3:"
        m = re.match(r"^Oberfl[aä]che\s+(\d+)(?:\.(\d+))?", s, re.I)
        if m:  # Gruppe 3
            return (3, int(m.group(1)), int(m.group(2) or 0), pos)

        # Unbekanntes → ganz ans Ende, stabil
        return (9, 10**9, 10**9, pos)

    return [l for _, _, _, _, l in sorted(((*key(l, i), l) for i, l in enumerate(lines)))]

def _append_surface_segments_aufmass(
    trench_no: int,
    seg_list: list[dict],
    aufmass: list[str],
    trench_length: float,
    trench_width: float,
    *,                         # ab hier nur Keyword-Args
    left_free: bool = True,    # außen links frei? (Randzone zur Länge addieren)
    right_free: bool = True,   # außen rechts frei?
) -> None:
    n = len(seg_list)
    remaining = float(trench_length)
    for k, s in enumerate(seg_list, start=1):
        off = float(s.get("offset", 0) or 0.0)
        raw_len = float(s.get("length", 0) or 0.0)
        if k < n and raw_len > 0:
            seg_len = min(raw_len, max(0.0, remaining))
        else:
            seg_len = max(0.0, remaining)

        add_left  = off if (k == 1 and left_free)  else 0.0
        add_right = off if (k == n and right_free) else 0.0
        length_adj = seg_len + add_left + add_right

        width_adj = float(trench_width) + 2.0 * off
        mat = s.get("material", "")
        aufmass.append(
            f"Oberfläche {trench_no}.{k}: Randzone={off} m  l={length_adj} m  b={width_adj} m"
            + (f"  Material={mat}" if mat else "")
        )
        remaining = max(0.0, remaining - seg_len)

def _get_manual_aufmass_lines(session: dict) -> Optional[list[str]]:
    elems = session.get("elements", [])
    # jüngsten Override nehmen
    for e in reversed(elems):
        if (e.get("type","").lower() == "aufmass_override"
            and isinstance(e.get("lines"), list)):
            # trimmen + leere raus
            return [str(x).strip() for x in e["lines"] if str(x).strip()]
    return None

def _set_manual_aufmass_lines(session: dict, lines: list[str]) -> None:
    # vorhandene Overrides entfernen (wir halten genau einen)
    elems = session.setdefault("elements", [])
    elems[:] = [e for e in elems if (e.get("type","").lower() != "aufmass_override")]
    elems.append({
        "type": "aufmass_override",
        "lines": [str(x).strip() for x in lines if str(x).strip()],
    })
# -----------------------------------------------------
# END HELPER ADD-MODE
# -----------------------------------------------------

# -----------------------------------------------------
# START HELPER EDIT-MODE
# -----------------------------------------------------
# --- Synonyme & Normalisierung --------------------------------------------
TYPE_ALIASES = {
    "druckrohr": "Rohr",
    "leitung": "Rohr",
    "kanal": "Rohr",
    "bg": "Baugraben",
    "graben": "Baugraben",
    "oberfläche": "Oberflächenbefestigung",
    "oberflaeche": "Oberflächenbefestigung",
    "pflaster": "Oberflächenbefestigung",
    "gehwegplatten": "Oberflächenbefestigung",
    "mosaiksteine": "Oberflächenbefestigung",
    "verbindung": "Verbindung",
    "verbinde": "Verbindung",
    "verbund": "Verbindung",
    "connect": "Verbindung",
}

FIELD_ALIASES = {
    "l": "length", "länge": "length", "laenge": "length",
    "b": "width",  "breite": "width",
    "t": "depth",  "tiefe": "depth",
    "dn": "diameter", "durchmesser": "diameter", "ø": "diameter", "diameter": "diameter",
    "randzone": "offset", "offset": "offset",
    "material": "material", "pattern": "pattern",
    "gok": "gok", "geländeoberkante": "gok", "gelaendeoberkante": "gok", "okg": "gok", "ok gelände": "gok", "ok gelaende": "gok",
}

_ALLOWED_EDIT_FIELDS = {"length","width","depth","diameter","material","offset","pattern","depth_left","depth_right","gok"}

FIELD_ALIASES.update({
  # Gefälle links/rechts
  "tl":"depth_left","t_l":"depth_left","tiefe_links":"depth_left","tlinks":"depth_left",
  "tr":"depth_right","t_r":"depth_right","tiefe_rechts":"depth_right","trechts":"depth_right",
  "gok": "gok", "geländeoberkante": "gok", "gelaendeoberkante": "gok", "okg": "gok", "ok gelände": "gok", "ok gelaende": "gok",
})

def _norm_text(s: str) -> str:
    return (s or "").strip().lower().replace("ä","ae").replace("ö","oe").replace("ü","ue").replace("ß","ss")

def _normalize_type_aliases(t: str) -> str:
    t0 = _norm_text(t)
    if t0 in TYPE_ALIASES: return TYPE_ALIASES[t0]
    if "oberflaeche" in t0 or "oberflaechen" in t0: return "Oberflächenbefestigung"
    if "durchstich" in t0: return "Durchstich"
    if "rohr" in t0: return "Rohr"
    if "baugraben" in t0 or "graben" in t0: return "Baugraben"
    return t

def _num_to_meters(x) -> Optional[float]:
    if isinstance(x, (int, float)):
        return float(x)
    s = _norm_text(str(x))
    s = s.replace(",", ".").strip()

    # DNxxx → Meter
    m = re.match(r"^dn\s*(\d+)\b", s)
    if m:
        return float(m.group(1)) / 1000.0

    # +/− zulassen: ±0.09 mm/cm/m oder ohne Einheit
    m = re.match(r"^[+-]?([0-9]*\.?[0-9]+)\s*(mm|cm|m)?$", s)
    if m:
        val = float(m.group(1))
        unit = (m.group(2) or "m")
        if unit == "mm": return val / 1000.0
        if unit == "cm": return val / 100.0
        return val

    # Fallback: ... mm irgendwo, mit optionalem Vorzeichen
    m = re.search(r"[+-]?([0-9]*\.?[0-9]+)\s*mm", s)
    if m:
        return float(m.group(1))/1000.0
    return None

def _to_meters(val) -> float:
    # gleiche Logik wie bei length/width/diameter: Komma -> Punkt, Einheiten m/cm/mm
    if val is None: return 0.0
    if isinstance(val, (int, float)): return float(val)
    s = str(val).strip().lower().replace(",", ".")
    mul = 1.0
    if s.endswith("mm"): mul, s = 0.001, s[:-2]
    elif s.endswith("cm"): mul, s = 0.01, s[:-2]
    elif s.endswith("m"):  mul, s = 1.0, s[:-1]
    try:
        return float(s) * mul
    except Exception:
        return 0.0

def _coerce_updates(upd: dict) -> dict:
    out = {}
    for k, v in (upd or {}).items():
        key = FIELD_ALIASES.get(_norm_text(k), k)
        if key in ("gok", "geländeoberkante", "gelaendeoberkante"):
            out["gok"] = _to_meters(v)
            continue

        if key in {"length","width","depth","diameter","offset"}:
            mv = _num_to_meters(v)
            if mv is not None:
                out[key] = mv
        elif key in {"material","pattern"}:
            out[key] = str(v)
    return out

# --- Heuristik: wenn Selection unvollständig/uneindeutig -------------------
def _resolve_selection_heuristic(elems: list[dict], sel: dict) -> Optional[int]:
    t = _normalize_type_aliases(sel.get("type",""))
    tn = t.lower()

    def matches(i,e):
        et = e.get("type","").lower()
        if tn == "baugraben":
            if "baugraben" not in et: return False
            ti = sel.get("trench_index")
            return (ti is None) or (int(e.get("trench_index",0)) == int(ti))
        if tn == "rohr":
            if "rohr" not in et: return False
            ft = sel.get("for_trench")
            return (ft is None) or (int(e.get("for_trench",0)) == int(ft))
        if "oberflächenbefest" in tn or "oberflaechenbefest" in tn:
            if ("oberflächenbefest" not in et) and ("oberflaechenbefest" not in et): return False
            ft = sel.get("for_trench"); seq = sel.get("seq")
            ok = True
            if ft is not None: ok &= int(e.get("for_trench",0)) == int(ft)
            if seq is not None: ok &= int(e.get("seq",0) or 0) == int(seq)
            return ok
        if tn == "durchstich":
            if "durchstich" not in et: return False
            b = sel.get("between")
            if b is None: return True
            return int(e.get("between", -1)) == int(b)
        return False

    cand = [i for i,e in enumerate(elems) if matches(i,e)]
    if len(cand) == 1:
        return cand[0]
    if len(cand) > 1:
        # nimm das zuletzt angelegte (stabil: letzter Treffer)
        return cand[-1]

    # Fallback: Typ alleine
    def typ(i,e):
        et = e.get("type","").lower()
        if tn == "baugraben": return "baugraben" in et
        if tn == "rohr": return "rohr" in et
        if tn == "durchstich": return "durchstich" in et
        return ("oberflächenbefest" in et) or ("oberflaechenbefest" in et)

    cand = [i for i,e in enumerate(elems) if typ(i,e)]
    if len(cand) == 1:
        return cand[0]
    if len(cand) > 1:
        return cand[-1]
    return None

def _build_edit_context(session: dict) -> str:
    elems = session.get("elements", [])
    def tnorm(e): return (e.get("type","") or "").lower()

    trenches = [e for e in elems if "baugraben" in tnorm(e)]
    pipes    = [e for e in elems if "rohr" in tnorm(e)]
    passes   = [e for e in elems if "durchstich" in tnorm(e)]
    surfs    = [e for e in elems if ("oberflächenbefest" in tnorm(e) or "oberflaechenbefest" in tnorm(e))]

    from collections import defaultdict
    surf_idx = defaultdict(list)
    for s in surfs:
        ft = int(s.get("for_trench", 0) or 0)
        seq = int(s.get("seq", 0) or 0)
        if ft: surf_idx[ft].append(seq)

    lines = []
    if trenches:
        lines.append("Baugräben: " + ", ".join(str(int(t.get("trench_index", i+1))) for i,t in enumerate(trenches)))
    if pipes:
        lines.append("Rohre in BG: " + ", ".join(sorted({str(int(p.get("for_trench",0))) for p in pipes if p.get("for_trench")})))
    if passes:
        lines.append("Durchstiche: " + ", ".join(sorted({f"{int(p.get('between',0))}-{int(p.get('between',0))+1}" for p in passes if p.get("between")})))
    if surf_idx:
        lines.append("Oberflächen: " + "; ".join(f"BG {k}: seq {sorted(v)}" for k,v in surf_idx.items()))
    return "\n".join(lines) or "keine"
# -----------------------------------------------------
# END HELPER EDIT-MODE
# -----------------------------------------------------

# -----------------------------------------------------
# START HELPER GEFÄLLE
# -----------------------------------------------------
def _coerce_updates(upd: dict) -> dict:
    out = {}
    for k, v in (upd or {}).items():
        key = FIELD_ALIASES.get(_norm_text(k), k)
        if key in {"length","width","depth","diameter","offset","depth_left","depth_right","gok"}:
            mv = _num_to_meters(v)
            if mv is not None:
                out[key] = mv
        elif key in {"material","pattern"}:
            out[key] = str(v)
    return out

# -----------------------------------------------------
# END HELPER GEFÄLLE
# -----------------------------------------------------

# -----------------------------------------------------
# 1) START SESSION
# -----------------------------------------------------
@app.post("/start-session")
def start_session():
    """
    Erzeugt eine neue Session-ID,
    legt in session_data[...] = {"elements":[]} ab,
    und gibt session_id zurück.
    """
    return session_manager.create_session()

@app.get("/session")
def get_session(session_id: str):
    session = session_manager.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session unknown")
    return session

@app.get("/get-aufmass-lines")
def get_aufmass_lines(session_id: str):
    session = session_manager.get_session(session_id)
    if session is None:
        raise HTTPException(404, "Session unknown")

    # 1) Falls es manuelle Zeilen gibt → diese zurück
    manual = _get_manual_aufmass_lines(session)
    if manual:
        return {"lines": manual}

    # 2) sonst letzten "aufmass"-Block (Auto) in Zeilen aufsplitten
    elems = session.get("elements", [])
    last_auto = next(
        (e for e in reversed(elems) if (e.get("type","").lower() == "aufmass")),
        None
    )
    text = (last_auto or {}).get("text", "") or ""
    # Header "Aufmaß:" entfernen und echte Zeilen liefern
    lines = [
        ln.strip() for ln in text.replace("\r","\n").split("\n")
        if ln.strip() and not ln.strip().lower().startswith("aufmaß")
    ]
    return {"lines": lines}

@app.post("/set-aufmass-lines")
def set_aufmass_lines(req: AufmassLinesRequest):
    session = session_manager.get_session(req.session_id)
    if session is None:
        raise HTTPException(404, "Session unknown")

    _set_manual_aufmass_lines(session, req.lines)
    session_manager.update_session(req.session_id, session)
    return {"status": "ok"}

# -----------------------------------------------------
# ADD ELEMENT
# -----------------------------------------------------
@app.post("/add-element")
def add_element(session_id: str, description: str = Body(..., embed=True)):
    session = session_manager.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session unknown")
    
    current_json = session

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
● Maße können mit „x“, „×“, „*“ oder „·“ getrennt sein (z. B. 6×0,9×1,10 m).
● Wenn nur zwei Maße genannt sind (z. B. „10×1,0 m“), interpretiere sie als length×width.
  Die Tiefe kommt dann aus „Tiefe …“/„links … rechts …“. Du MUSST im JSON immer auch
  das Feld "depth" setzen: depth = max(depth_left, depth_right).

REFERENZIERUNG (sehr wichtig)
• "trench_index" NUR bei type == "Baugraben".
• Rohr und Oberflächenbefestigung: IMMER "for_trench": <1-basiger Ziel-Baugraben>.
• Durchstich: IMMER "between": N  (zwischen Baugraben N und N+1). KEIN "for_trench".
• Formulierungen wie „im zweiten Baugraben“, „zu Baugraben 2“, „in BG 3“
  bedeuten: setze "for_trench" = N (nur für Rohr/Oberfläche).
• Erfinde KEINE neuen Baugräben. Wenn die Anweisung auf einen nicht existierenden
  Baugraben verweist, erzeuge KEIN Element und schreibe das im "answer".

────────────────────────────────────────────
ROHR-REGELN (Pflicht)
────────────────────────────────────────────
• Für type="Rohr" gilt:
  – "for_trench": Ziel-Baugraben (1-basiert) ist Pflicht.
  – "diameter": Pflicht (z. B. DN150 → 0,15).
  – "length": Pflicht, **außer** der Text enthält ausdrücklich
    „über die gesamte Länge“/„über die volle Länge“.
    In diesem Sonderfall setze: "full_span": true und lasse "length" weg.
  – "offset": optionaler Startversatz ab linker Innenkante (Meter).
  – Wenn KEINE Länge genannt ist (z. B. „Zeichne Druckrohr DN300 in BG 2“),
    INTERPRETIERE das als „über die gesamte Länge“ und setze "full_span": true.
    (Synonyme: „einziehen“, „einlegen“, „entlang des Baugrabens“, „auf ganzer Länge“,
    „komplette Länge“, „volle Länge“)
• Beispiel 1:
  „Zeichne Druckrohr mit Länge 6 m in BG 1, DN150.“
  → {{"type":"Rohr","for_trench":1,"diameter":0.15,"length":6.0}}
• Beispiel 2:
  „Zeichne Druckrohr mit 6 m Länge und Versatz 2 m in BG 2.“
  → {{"type":"Rohr","for_trench":2,"diameter":0.15,"length":6.0,"offset":2.0}}
• Beispiel 3:
  „Zeichne Druckrohr über die gesamte Länge in BG 1, DN200.“
  → {{"type":"Rohr","for_trench":1,"diameter":0.20,"full_span":true}}

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
GELÄNDEOBERKANTE (GOK)
────────────────────────────────────────────
• Für type="Baugraben" darf optional "gok" angegeben werden.
• Akzeptiere Schreibweisen wie: "GOK +0,09", "Geländeoberkante -0,05", "OK Gelände 0,12".
• Werte in Metern; Plus/Minus ist erlaubt.
• Wenn KEIN GOK-Wert im Text steht → setze "gok": 0.0.

────────────────────────────────────────────
MEHRERE OBERFLÄCHEN PRO BAUGRABEN (STUFUNG)
────────────────────────────────────────────
• Oberflächen werden als mehrere Objekte mit type="Oberflächenbefestigung"
  und identischem "for_trench" erzeugt.
• Für jede Oberfläche setze:
    – offset  (Randzone in Metern, Pflicht)
    – length  (Segmentlänge in Metern; die letzte darf fehlen → Restlänge)
    – material (optional)
    – seq     (1-basiert, Reihenfolge der Segmente von links nach rechts)
• Beispiel:
    "Baugraben 1 hat zwei Oberflächen:
     Oberfläche 1: Randzone 0,2, Länge 5 m, Material: Mosaiksteine.
     Oberfläche 2: Randzone 0,5, (Rest), Material: Gehwegplatten."
  ⇒
  [
    {{"type":"Oberflächenbefestigung","for_trench":1,"seq":1,"offset":0.2,"length":5.0,"material":"Mosaiksteine"}},
    {{"type":"Oberflächenbefestigung","for_trench":1,"seq":2,"offset":0.5,           "material":"Gehwegplatten"}}
  ]

────────────────────────────────────────────
DURCHSTICH-REGEL
────────────────────────────────────────────
• Für "Durchstich" ist ausschließlich die **Länge** Pflicht.
• Schreibe die Länge in das Feld **"length"**.
• Das Feld **"width"** darf NICHT gesetzt werden.
• Ein ggf. genannter "Versatz/offset" wird ignoriert.
• Der Durchstich liegt immer zwischen dem benachbarten Baugraben N und N+1.
• Formulierungen wie „Verbinde Baugraben X und Y mit Durchstich …“
  → Prüfe: |X−Y| == 1 (benachbart). Falls ja:
     {{ "type":"Durchstich", "length": <L>, "between": min(X, Y) }}.
    Falls nein: Erzeuge **kein** Element und schreibe in "answer",
    dass nur benachbarte Baugräben erlaubt sind.
• Sätze im Perfekt/Präteritum („… wurde erstellt“, „… ist erstellt“)
  sind als Auftrag zu verstehen (Element erzeugen).

Beispiel A:
  "Verbinde Baugraben 1 und 2 mit Durchstich l=2"
→ {{ "type":"Durchstich", "length":2.0, "between":1 }}

Beispiel B:
  "Ein Durchstich mit einer Länge von 3 Metern wurde zwischen dem zweiten und dritten Baugraben erstellt."
→ {{ "type":"Durchstich", "length":3.0, "between":2 }}

────────────────────────────────────────────
VERBINDUNG OHNE DURCHSTICH
────────────────────────────────────────────
• Verwende type="Verbindung", wenn Baugräben ohne Durchstich verbunden werden sollen.
• Adressierung:
    – Verbindung zwischen BG N und N+1:  {{"type":"Verbindung","between": N}}
    – Liste: „Verbinde Baugraben 1, 2 und 3“
        → erzeuge zwei Objekte:
           {{"type":"Verbindung","between":1}},
           {{"type":"Verbindung","between":2}}
• Es sind nur benachbarte Gräben erlaubt (|X−Y|==1). Nicht-benachbarte Paare werden ignoriert.
• Verbindungen sind exklusiv: Falls an derselben Naht ein Durchstich existiert, hat der Durchstich Vorrang.
• Verbindungen erzeugen KEINE Aufmaß-Zeilen.

────────────────────────────────────────────
GEFÄLLE (optional)
────────────────────────────────────────────
• Für Baugräben dürfen zusätzlich "depth_left" und "depth_right" gesetzt werden.
• Wenn nur "depth" angegeben ist → beide Seiten gleich.
• Wenn depth_left/right gesetzt sind, MUSS "depth" = max(depth_left, depth_right) im Objekt stehen.
Beispiel:
  „Baugraben 5x5m, Tiefe links 1,10 m, rechts 1,03 m“
→ {{ "type":"Baugraben","length":5,"width":5,"depth_left":1.10,"depth_right":1.03,"depth":1.10 }}

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
* Durchstich ⇒ length ist Pflicht; width nicht verwenden.

{{
  "elements": [
    {{
      "type": "string",  # Baugraben | Rohr | Oberflächenbefestigung | Durchstich | Verbindung
      "trench_index": 0, # NUR bei Baugraben
      "for_trench": 0,   # NUR bei Nicht-Baugraben (1-basiger Verweis)
      "seq": 0,          # optional: laufende Nummer innerhalb desselben Typs
      
      "length": 0.0,
      "width":  0.0,
      "depth":  0.0,
      "depth_left":  0.0,      // optional für Gefälle
      "depth_right": 0.0,      // optional für Gefälle
      "diameter": 0.0,

      "material": "",
      "offset":   0.0,
      "pattern":  "",      # nur für Durchstich (Schraffur-Name)
      "gok":      0.0 
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
• Erzeuge KEINE Baugräben implizit. Neues "trench_index" nur setzen, wenn der
  Nutzer ausdrücklich einen Baugraben anlegt.

────────────────────────────────────────────
ANTWORTFORMAT  (genau so!)
────────────────────────────────────────────
{{
  "new_elements": [  ...NEUE Objekte...  ],
  "answer": "<max. 2 Sätze>"
}}
"""

    resp = client.chat.completions.create(
        model            = "qwen/qwen3-coder",
        response_format  = {"type": "json_object"},
        temperature      = 0.0,
        max_tokens       = 500,
        messages = [
            {"role": "system", "content": "You are a JSON API."},
            {"role": "user",   "content": prompt},
        ],
    )
    new_json = json.loads(resp.choices[0].message.content)

    added = new_json.get("new_elements") or new_json.get("elements") or []

    for el in added:
        t = (el.get("type","") or "").lower()
        if "baugraben" in t:
            if "gok" not in el or el["gok"] is None or el["gok"] == "":
                el["gok"] = 0.0
            else:
                mv = _num_to_meters(el["gok"])
                el["gok"] = mv if mv is not None else 0.0

        if not isinstance(added, list):
            raise HTTPException(400, "Antwort enthielt keine Element-Liste")

    # ⑤ Session aktualisieren
    current_json["elements"].extend(added)
    _normalize_and_reindex(current_json)
    session_manager.update_session(session_id, current_json)

    # Dann an den Client beides zurücksenden
    return {
        "status"      : "ok",
        "updated_json": current_json,
        "answer"      : new_json.get("answer", "")
    }

# -----------------------------------------------------
#  DXF generieren und Session aktualisieren
# -----------------------------------------------------
@app.post("/generate-dxf-by-session")
def generate_dxf_by_session(session_id: str):
    # 1) Session laden --------------------------------
    session = session_manager.get_session(session_id)
    if session is None:
        raise HTTPException(404, "Session unknown")

    try:
        # 2) DXF + Aufmaß erzeugen ---------------------
        dxf_file, aufmass_txt = _generate_dxf_intern(session)

        # 3) Aufmaß in die Session einhängen -----------
        session.setdefault("elements", [])
        session["elements"].append({
            "type": "aufmass",
            "text": aufmass_txt,
        })
        session_manager.update_session(session_id, session)

        # 4) Datei zurückgeben -------------------------
        return FileResponse(
            dxf_file,
            media_type="application/dxf",
            filename=os.path.basename(dxf_file),
        )

    except Exception as e:
        raise HTTPException(500, f"DXF-Fehler: {e}")

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
    doc.header["$PLINEGEN"] = 1.0

    # ---------- Elemente vorsortieren ----------
    trenches, pipes, surfaces, passes, joins = [], [], [], [], []
    for el in parsed_json.get("elements", []):
        t = el.get("type", "").lower()
        if "baugraben"           in t: trenches.append(el)
        elif "rohr"              in t: pipes.append(el)
        elif "oberflächenbefest" in t: surfaces.append(el)
        elif "durchstich"        in t: passes.append(el)
        elif "verbindung"        in t: joins.append(el)

    join_set = {int(j.get("between", 0) or 0) for j in joins if j.get("between") is not None}

    def _has_link_between(seam_1based: int) -> bool:
        return (_pass_for_between(passes, seam_1based) is not None) or (seam_1based in join_set)

    if not trenches:
        raise HTTPException(400, "Kein Baugraben vorhanden – bitte zuerst /add-element benutzen.")

    # ---------- Layout-Konstanten ----------
    CLR_LR    = 0.20    # freier Rand links/rechts (Front)
    CLR_BOT   = 0.20    # freier Rand unten
    GAP_BG    = 1.50    # Abstand zwischen zwei Baugräben
    TOP_SHIFT = 1.50    # Abstand Draufsicht → Vorderansicht
    PASS_BOTTOM_GAP = 0.5
    PASS_SYMBOL_H = 0.40  # sichtbare Höhe des Durchstich-Rechtecks in der Vorderansicht
    PASS_DIM_OFFSET = 0.50   # Abstand der Maßlinie über der Oberkante des Durchstichs
    GOK_DIM_XSHIFT = 0.35   # X-Versatz der GOK-Maßlinie nach links

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

    def _depths(bg: dict) -> tuple[float, float, float]:
        d  = float(bg.get("depth") or 0.0)
        dL = float(bg.get("depth_left",  d))
        dR = float(bg.get("depth_right", d))
        return max(d, dL, dR), dL, dR  # (ref, left, right)

    def _append_trench_line(aufmass, idx, L, B, d_ref, dL, dR):
        line = ""
        if abs(dL - dR) < 1e-9:
            line = f"Baugraben {idx}: l={L} m  b={B} m  t={d_ref} m"
        else:
            line = f"Baugraben {idx}: l={L} m  b={B} m  t_links={dL} m  t_rechts={dR} m"

        # NEU: GOK optional anhängen
        bg = trenches[idx-1]
        gok = float(bg.get("gok") or 0.0)
        if abs(gok) > 1e-9:
            sign = "+" if gok >= 0 else ""
            line += f"  GOK={sign}{gok} m"
        aufmass.append(line)

    # Hilfsfunktion am Anfang von _generate_dxf_intern definieren (oder lokal im Block):
    def _is_join_only(seam_idx: int) -> bool:
        # True, wenn an Naht seam_idx nur "Verbindung" existiert (kein Durchstich)
        return (seam_idx in join_set) and (_pass_for_between(passes, seam_idx) is None)

    def _pipe_full_and_want(pipe: dict):
        full = str(pipe.get("full_span", "")).lower() in ("true","1","yes")
        L = float(pipe.get("length", 0) or 0.0)
        if not full and L <= 0:
            full = True
        return full, (None if full else L)

    def _hatch_poly(pts):
        h = msp.add_hatch(dxfattribs={"layer": LAYER_HATCH})
        angle = 45.0 if HATCH_PATTERN.upper() == "EARTH" else 0.0
        h.set_pattern_fill(HATCH_PATTERN, scale=HATCH_SCALE, angle=angle)
        cx = sum(p[0] for p in pts)/len(pts); cy = sum(p[1] for p in pts)/len(pts)
        try: h.set_pattern_origin((cx, cy))
        except AttributeError: pass
        h.paths.add_polyline_path(pts, is_closed=True)

    # NEU: Rechteck-Symbol für Durchstich (Frontansicht)
    def _draw_pass_symbol_rect(x0: float, x1: float, *, pattern: str | None = None, y_top: float | None = None):
        if x1 - x0 <= 1e-9:
            return
        if y_top is None:
            # Fallback: unten ankern (alt)
            y0 = CLR_BOT + PASS_BOTTOM_GAP
            y1 = y0 + PASS_SYMBOL_H
        else:
            # NEU: oben ankern
            y1 = y_top   # knapp unter der Oberkante „kleben“
            y0 = y1 - PASS_SYMBOL_H

        msp.add_lwpolyline([(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)],
                        dxfattribs={"layer": LAYER_TRENCH_OUT})
        pat = (pattern or HATCH_PATTERN)
        h = msp.add_hatch(dxfattribs={"layer": LAYER_HATCH})
        angle = 45.0 if pat.upper() == "EARTH" else 0.0
        h.set_pattern_fill(pat, scale=HATCH_SCALE, angle=angle)
        try: h.set_pattern_origin(((x0+x1)/2, (y0+y1)/2))
        except AttributeError: pass
        h.paths.add_polyline_path([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], is_closed=True)

    # --- Neu: Positions- & Duplikat-Tracking ---
    trench_origin_x: dict[int, float] = {}  # 0-basierter Index -> Außen-Links-X in der Vorderansicht
    drawn_top: set[int] = set()             # 1-basierte Indizes: Draufsicht schon gezeichnet?
    drawn_pipe: set[int] = set()            # 1-basierte Indizes: Rohr schon gezeichnet?
    drawn_surface: set[int] = set()         # 1-basierte Indizes: Oberflächen schon gezeichnet?
    printed_trench: set[int] = set()        # 1-basierte Indizes: Aufmaß "Baugraben N" schon geschrieben?
    printed_pass: set[int] = set()          # 1-basierte Naht-Indizes: Aufmaß "Durchstich N" schon geschrieben?
    printed_depth: set[int] = set()
    printed_gok: set[int] = set()          # 1-basierte Indizes: Aufmaß "GOK N" schon geschrieben?
    skip_single_next = False                # rechter Graben des letzten Merges schon gezeichnet -> Solo überspringen

    # --- Tiefenmaße im Merge (Durchstich ODER Verbindung) ----------------------
    def _add_depth_dim(x_col, y_top, depth, base_x):
        msp.add_linear_dim(
            base=(base_x, CLR_BOT),
            p1=(x_col, y_top),
            p2=(x_col, y_top - depth),
            angle=90,
            override={
                "dimtxt": DIM_TXT_H,
                "dimclrd": 3,
                "dimexe": DIM_EXE_OFF,
                "dimexo": DIM_EXE_OFF,
                "dimtad": 0,
            },
            dxfattribs={"layer": LAYER_TRENCH_OUT},
        ).render()

    def _add_pass_len_dim(x0: float, x1: float, y_ref: float):
        """
        Horizontales Maß über dem Durchstich (x0..x1) an der Oberkante y_ref.
        """
        y_dim = y_ref + PASS_DIM_OFFSET
        msp.add_linear_dim(
            base=(x0, y_dim),           # Lage der Maßlinie (y-Distanz ist entscheidend)
            p1=(x0, y_ref),             # linker Bezugspunkt
            p2=(x1, y_ref),             # rechter Bezugspunkt
            angle=0,                    # horizontal
            override={
                "dimtxt":  DIM_TXT_H,   # Textgröße wie bei deinen anderen Maßen
                "dimclrd": 3,           # grün wie in deinen Screenshots
                "dimexe":  DIM_EXE_OFF, # keine Überstände
                "dimexo":  DIM_EXE_OFF, # kein Abstand der Hilfslinien
                "dimtad":  0,           # Text auf der Maßlinie
            },
            dxfattribs={"layer": LAYER_TRENCH_OUT},
        ).render()

    def _same(a, b, eps=1e-6):
        return abs(float(a) - float(b)) < eps

    def _add_gok_dim(x_col: float, y_top: float, gok_val: float, side: str = "left"):
        if abs(gok_val) < 1e-9:
            return
        y_ref = CLR_BOT + MAX_DEPTH  # globale Oberkante ohne GOK
        base_x = (x_col - (DIM_OFFSET_FRONT + GOK_DIM_XSHIFT)) if side == "left" \
                else (x_col + (DIM_OFFSET_FRONT + GOK_DIM_XSHIFT))
        sign = "+" if gok_val >= 0 else "-"
        msp.add_linear_dim(
            base=(base_x, y_ref),
            p1=(x_col, y_ref),
            p2=(x_col, y_top),
            angle=90,
            override={
                "dimtxt":  DIM_TXT_H,
                "dimclrd": 3,
                "dimexe":  DIM_EXE_OFF,
                "dimexo":  DIM_EXE_OFF,
                "dimtad":  0,
                "dimpost": f"GOK {sign}<> m",
            },
            dxfattribs={"layer": LAYER_TRENCH_OUT},
        ).render()

    # NEU: gemeinsamer Bottom-Offset je Graben, damit die Oberkante auf gleicher Höhe liegt
    def _base_y(depth_ref: float) -> float:
        depth_ref = float(depth_ref or 0.0)
        return CLR_BOT + (MAX_DEPTH - depth_ref)

    # --- GOK-Helper ---------------------------------------------------------
    def _gok(bg: dict) -> float:
        try:
            return float(bg.get("gok") or 0.0)
        except Exception:
            return 0.0

    # Referenztiefe (bleibt wie bisher)
    def _depths(bg: dict) -> tuple[float, float, float]:
        d  = float(bg.get("depth") or 0.0)
        dL = float(bg.get("depth_left",  d))
        dR = float(bg.get("depth_right", d))
        return max(d, dL, dR), dL, dR  # (ref, left, right)

    # Basis-Y der Vorderansicht unter Berücksichtigung von GOK:
    # sorgt weiterhin dafür, dass ohne GOK alle Oberkanten auf gleicher y liegen,
    # mit GOK jedoch je Graben um gok verschoben sind.
    def _base_y_with_gok(depth_ref: float, gok_val: float) -> float:
        return CLR_BOT + (MAX_DEPTH - depth_ref) + gok_val

    MAX_DEPTH = max(_depths(t)[0] for t in trenches)
    MAX_GOK   = max(_gok(t) for t in trenches) if trenches else 0.0
    EPS = 1e-3  # ~1 mm Anti-Z-Fighting

    Y_TOP = CLR_BOT + TOP_SHIFT + MAX_DEPTH + max(0.0, MAX_GOK)

    # ---------- Hauptschleife über alle Baugräben ----------
    i = 0
    while i < len(trenches):
        # --- Basisdaten des linken Grabens ---
        bg1 = trenches[i]
        L1 = float(bg1.get("length", 0) or 0)
        B1 = float(bg1.get("width", 0) or 0)
        T1_ref, T1_L, T1_R = _depths(bg1)

        # linke Außen-X dieses Grabens: entweder schon gesetzt (aus vorherigem Merge),
        # sonst aktueller cursor_x
        x_start = trench_origin_x.get(i, cursor_x)

        # Gibt es direkt rechts von BG i einen Durchstich?
        pas = _pass_for_between(passes, i+1)
        has_neighbor = (i+1 < len(trenches))
        merge_next = has_neighbor and _has_link_between(i+1)

        # --------------------------------------------------
        # A) Kein Durchstich zwischen i und i+1
        # --------------------------------------------------
        if not merge_next:
            if skip_single_next:
                skip_single_next = False
                i += 1
                continue

            # NEU: individueller Bottom-Offset, damit y_top = CLR_BOT + MAX_DEPTH
            base1 = _base_y_with_gok(T1_ref, _gok(bg1))
            oy1   = base1 - 0.2

            y_in_left  = base1 + (T1_ref - T1_L)
            y_in_right = base1 + (T1_ref - T1_R)

            # Solo (kein Merge zur rechten Seite)
            draw_trench_front(
                msp, (x_start, oy1), L1, T1_ref,
                clearance_left=CLR_LR,
                clearance_bottom=0.2,
                depth_left=T1_L, depth_right=T1_R,
            )

            # Draufsicht (Top) – bleibt wie bisher auf gemeinsamer Y_TOP
            if (i+1) not in drawn_top:
                draw_trench_top(
                    msp, (x_start+CLR_LR, Y_TOP),
                    length=L1, width=B1,
                    dim_right=(i + 1 == len(trenches))
                )
                drawn_top.add(i+1)

            # Rohr: ebenfalls vom individuellen Bottom-Offset starten
            pipe = _first_pipe_for_trench(pipes, i+1)
            if pipe:
                d = float(pipe.get("diameter", 0) or 0)
                if d > 0 and (i+1) not in drawn_pipe:
                    off = float(pipe.get("offset", 0) or 0.0)
                    full, want = _pipe_full_and_want(pipe)
                    eff = draw_pipe_front(
                        msp,
                        origin_front=(x_start + CLR_LR, base1),
                        trench_inner_length=L1,
                        diameter=d,
                        span_length=want,
                        offset=off,
                        bottom_y_left=y_in_left,
                        bottom_y_right=y_in_right,
                    )
                    if eff > 0:
                        aufmass.append(
                            f"Rohr {i+1}: l={eff} m  Ø={d} m" + (f"  Versatz={off} m" if off else "")
                        )
                        drawn_pipe.add(i+1)

            # Oberflächen je Graben nur einmal
            seg_list = _surfaces_for_trench(surfaces, i+1)
            if seg_list and (i+1) not in drawn_surface:
                if any(float(s.get("length", 0) or 0) > 0 for s in seg_list):
                    draw_surface_top_segments(
                        msp,
                        trench_top_left=(x_start+CLR_LR, Y_TOP),
                        trench_length=L1, trench_width=B1,
                        segments=[{
                            "length": float(s.get("length", 0) or 0),
                            "offset": float(s.get("offset", 0) or 0),
                            "material": s.get("material", "")
                        } for s in seg_list],
                        add_dims=True,
                    )
                    _append_surface_segments_aufmass(i+1, seg_list, aufmass, L1, B1)
                else:
                    off = float(seg_list[0].get("offset", 0) or 0)
                    if off:
                        draw_surface_top(
                            msp,
                            trench_top_left=(x_start+CLR_LR, Y_TOP),
                            trench_length=L1, trench_width=B1,
                            offset=off,
                            material_text=f"Oberfläche: {seg_list[0].get('material','')}",
                        )
                        mat = seg_list[0].get("material","")
                        len_total = L1 + 2*off
                        width_total = B1 + 2*off
                        aufmass.append(
                            f"Oberfläche {i+1}: Randzone={off} m  l={len_total} m  b={width_total} m"
                            + (f"  Material={mat}" if mat else "")
                        )
                drawn_surface.add(i+1)

            # Aufmaß Baugraben nur einmal
            if (i+1) not in printed_trench:
                _append_trench_line(aufmass, i+1, L1, B1, T1_ref, T1_L, T1_R)
                printed_trench.add(i+1)

            # Bookkeeping / Cursor
            trench_origin_x.setdefault(i, x_start)
            cursor_x = max(cursor_x, x_start + L1 + 2*CLR_LR + GAP_BG)
            i += 1
            continue

        # --------------------------------------------------
        # B) Verbindung/Durchstich zwischen i und i+1 (Merge-Zeichnung)
        # --------------------------------------------------
        bg2 = trenches[i+1]
        L2 = float(bg2.get("length", 0) or 0)
        B2 = float(bg2.get("width", 0) or 0)
        T2_ref, T2_L, T2_R = _depths(bg2)

        # Gibt es an der Naht nur eine Verbindung (ohne Durchstich)?
        join_only = (_pass_for_between(passes, i+1) is None) and ((i+1) in join_set)
        pas = None if join_only else _pass_for_between(passes, i+1)
        if not join_only and (not pas or "length" not in pas):
            raise HTTPException(400, "Durchstich ohne Länge (erwarte Feld 'length').")
        p_w  = 0.0 if join_only else float(pas["length"])
        p_off = L1  # Pass startet direkt an der Naht (Innenkante BG1)

        # Cluster-Kontext (Nachbarn links/rechts?)
        has_pass_left  = (i > 0) and _has_link_between(i)
        has_pass_right = (i+2 < len(trenches)) and _has_link_between(i+2)

        left_clear  = 0.0 if has_pass_left  else CLR_LR
        right_clear = 0.0 if has_pass_right else CLR_LR

        baseL = _base_y_with_gok(T1_ref, _gok(bg1))
        baseR = _base_y_with_gok(T2_ref, _gok(bg2))
        yTopL = baseL + T1_ref
        yTopR = baseR + T2_ref


        xL            = x_start
        x_inner_left  = x_start + left_clear
        xSeam         = x_inner_left + L1                     # Naht (Innenkante BG1 rechts)
        xRightStart   = xSeam + p_w                           # Start der rechten Innenkante (ggf. nach Pass)
        x_inner_right = xRightStart + L2
        xR            = x_inner_right + right_clear

        # -----------------------------
        # Draufsichten (nur bei reiner Verbindung an der Naht clippen)
        # -----------------------------
        top_left_1 = (x_inner_left, Y_TOP)
        top_left_2 = (xRightStart,  Y_TOP)

        join_L = _is_join_only(i) if i > 0 else False
        join_M = True
        join_R = _is_join_only(i + 2) if (i + 2) < len(trenches) else False

        if (i + 1) not in drawn_top:
            draw_trench_top(
                msp, top_left_1, length=L1, width=B1,
                clip_left=join_L,          # vorher: (join_L and join_only)
                clip_right=join_only,      # vorher: (join_M and join_only)
                dim_right=False
            )
            drawn_top.add(i + 1)

        # --- Draufsicht Baugraben 2 (rechts vom aktuellen Merge 1|2)
        if (i + 2) not in drawn_top:
            draw_trench_top(
                msp, top_left_2, length=L2, width=B2,
                clip_left=join_only,       # vorher: (join_M and join_only)
                clip_right=join_R,         # vorher: (join_R and join_only)
                dim_right=(i + 2 == len(trenches))
            )
            drawn_top.add(i + 2)

        # --- GOK-Bemaßung NUR bei verbundenen Baugräben ---
        gokL = _gok(bg1)
        if abs(gokL) > 1e-9 and (i+1) not in printed_gok:
            _add_gok_dim(x_inner_left, yTopL, gokL, side="left")
            printed_gok.add(i+1)

        gokR = _gok(bg2)
        if abs(gokR) > 1e-9 and (i+2) not in printed_gok:
            _add_gok_dim(x_inner_right, yTopR, gokR, side="right")
            printed_gok.add(i+2)

        # --- Nahtlinie in der Draufsicht (nur im NICHT-überlappenden Bereich) ---
        # In der Überlappung (0 .. min(B1,B2)) gibt es KEINE Linie.
        # Die Linie schließt nur die Stufe: min(B1,B2) .. max(B1,B2).
        if join_only and abs(B1 - B2) > 1e-9:
            y_lo = Y_TOP + min(B1, B2)
            y_hi = Y_TOP + max(B1, B2)
            if y_hi - y_lo > 1e-9:
                msp.add_lwpolyline([(xSeam, y_lo), (xSeam, y_hi)],
                                dxfattribs={"layer": LAYER_TRENCH_OUT})

        # -----------------------------
        # Oberflächen (einmal je BG, an der Naht nur bei Verbindung clippen)
        # -----------------------------
        seg_list_L = _surfaces_for_trench(surfaces, i+1)
        if seg_list_L and (i+1) not in drawn_surface:
            if any(float(s.get("length", 0) or 0) > 0 for s in seg_list_L):
                draw_surface_top_segments(
                    msp, trench_top_left=top_left_1, trench_length=L1, trench_width=B1,
                    segments=[{
                        "length":  float(s.get("length", 0) or 0),
                        "offset":  float(s.get("offset", 0) or 0),
                        "material": s.get("material", "")
                    } for s in seg_list_L],
                    add_dims=True,
                    clip_left=join_L,
                    clip_right=join_only,
                )
                _append_surface_segments_aufmass(
                    i+1, seg_list_L, aufmass, L1, B1,
                    left_free=not join_L, right_free=False
                )
            else:
                offL = float(seg_list_L[0].get("offset", 0) or 0)
                if offL:
                    draw_surface_top(
                        msp, trench_top_left=top_left_1, trench_length=L1, trench_width=B1,
                        offset=offL,
                        clip_left=(join_L and join_only),
                        clip_right=(join_M and join_only),
                        material_text=f"Oberfläche: {seg_list_L[0].get('material','')}",
                    )
                    matL = seg_list_L[0].get("material","")
                    aufmass.append(
                        f"Oberfläche {i+1}: Randzone={offL} m  l={L1+2*offL} m  b={B1+2*offL} m"
                        + (f"  Material={matL}" if matL else "")
                    )
            drawn_surface.add(i+1)

        seg_list_R = _surfaces_for_trench(surfaces, i+2)
        if seg_list_R and (i+2) not in drawn_surface:
            if any(float(s.get("length", 0) or 0) > 0 for s in seg_list_R):
                draw_surface_top_segments(
                    msp, trench_top_left=top_left_2, trench_length=L2, trench_width=B2,
                    segments=[{
                        "length":  float(s.get("length", 0) or 0),
                        "offset":  float(s.get("offset", 0) or 0),
                        "material": s.get("material", "")
                    } for s in seg_list_R],
                    add_dims=True,
                    clip_left=join_only,
                    clip_right=join_R,
                )
                _append_surface_segments_aufmass(
                    i+2, seg_list_R, aufmass, L2, B2,
                    left_free=False, right_free=not join_R
                )
            else:
                offR = float(seg_list_R[0].get("offset", 0) or 0)
                if offR:
                    draw_surface_top(
                        msp, trench_top_left=top_left_2, trench_length=L2, trench_width=B2,
                        offset=offR,
                        clip_left=(join_M and join_only),
                        clip_right=(join_R and join_only),
                        material_text=f"Oberfläche: {seg_list_R[0].get('material','')}",
                    )
                    matR = seg_list_R[0].get("material","")
                    aufmass.append(
                        f"Oberfläche {i+2}: Randzone={offR} m  l={L2+2*offR} m  b={B2+2*offR} m"
                        + (f"  Material={matR}" if matR else "")
                    )
            drawn_surface.add(i+2)

        # -----------------------------
        # INNENKONTUREN (ohne Basislinie)
        # -----------------------------
        # links
        draw_trench_front_lr(
            msp, (x_start, baseL - CLR_BOT), L1, T1_ref,
            clear_left=left_clear, clear_right=0.0,
            draw_left_inner=not has_pass_left, draw_right_inner=False,
            draw_outer=False,   # <— war True
            draw_bottom=False,
            depth_left=T1_L, depth_right=T1_R
        )

        # rechts
        draw_trench_front_lr(
            msp, (xRightStart, baseR - CLR_BOT), L2, T2_ref,
            clear_left=0.0, clear_right=right_clear,
            draw_left_inner=False, draw_right_inner=not has_pass_right,
            draw_outer=False,   # <— war True
            draw_bottom=False,
            depth_left=T2_L, depth_right=T2_R
        )

        # -----------------------------
        # AUSSENKONTUR (einmal, mit Stufe)
        # -----------------------------
        y_in_L = baseL + (T1_ref - T1_L)
        y_in_R = baseR + (T2_ref - T2_R)

        # Unterer Außenrand = Innenboden - CLR_BOT
        y_out_L = y_in_L - CLR_BOT
        y_out_R = y_in_R - CLR_BOT

        # Richtung der Außenstufe:
        # - join_only: Stufe an der Naht; Versatz horizontal um CLR_LR
        # - Wenn rechts tiefer (y_out_R < y_out_L), nach links versetzen (-CLR_LR)
        # - Wenn links tiefer oder gleich, nach rechts versetzen (+CLR_LR)
        step_dir = 0.0
        if join_only:
            step_dir = (CLR_LR if (y_out_L <= y_out_R + 1e-9) else -CLR_LR)

        x_step_out = xSeam + step_dir  # X-Position der Außen-Stufenvertikalen

        # Außen-Vertikalen nur ganz links/rechts
        if not has_pass_left:
            msp.add_lwpolyline([(xL, y_out_L), (xL, yTopL)], dxfattribs={"layer": LAYER_TRENCH_OUT})
        
        if not has_pass_right:
            msp.add_lwpolyline([(xR, y_out_R), (xR, yTopR)], dxfattribs={"layer": LAYER_TRENCH_OUT})

        # --- Innen-/Außenboden-Niveaus LINKS/RECHTS (jeweils am Rand bzw. an der Naht)
        y_in_L_left   = baseL + (T1_ref - T1_L)
        y_in_L_right  = baseL + (T1_ref - T1_R)
        y_in_R_left   = baseR + (T2_ref - T2_L)
        y_in_R_right  = baseR + (T2_ref - T2_R)

        y_out_L_left  = y_in_L_left  - CLR_BOT
        y_out_L_right = y_in_L_right - CLR_BOT   # Nahtseite links
        y_out_R_left  = y_in_R_left  - CLR_BOT   # Nahtseite rechts
        y_out_R_right = y_in_R_right - CLR_BOT

        # Gibt es an der Naht wirklich eine Außen-Stufe?
        #has_step = join_only and (abs(y_out_L_right - y_out_R_left) > 1e-9)
        has_step = abs(y_out_L_right - y_out_R_left) > 1e-9
        step_dir_R = (CLR_LR if (y_out_L_right <= y_out_R_left + 1e-9) else -CLR_LR) if has_step else 0.0

        x_join_R = xSeam + (step_dir_R if has_step else 0.0)

        # Stufen-Vertikale an linker / rechter Lückenkante (bei Verbindung identisch)
        x_step_out_L = xSeam       + step_dir_R
        x_step_out_R = xRightStart + step_dir_R

        step_dir = 0.0
        if has_step:
            # bei tieferem rechten Teil nach links versetzen, sonst nach rechts
            step_dir = (CLR_LR if (y_out_L_right <= y_out_R_left + 1e-9) else -CLR_LR)
        x_step_out = xSeam + step_dir

        # --- OBERKANTE (Front) exakt wie in der Draufsicht zeichnen ---
        has_top_step = abs(yTopL - yTopR) > 1e-9

        # Enden der Top-Linien liegen an den Naht-X (nicht an x_step_out_*)
        x_left_end_top    = xSeam
        x_right_start_top = xRightStart   # bei Verbindung = xSeam, bei Durchstich echte Lücke

        # linkes Top-Stück
        msp.add_lwpolyline([(xL, yTopL), (x_left_end_top, yTopL)],
                        dxfattribs={"layer": LAYER_TRENCH_OUT})

        # rechtes Top-Stück
        msp.add_lwpolyline([(x_right_start_top, yTopR), (xR, yTopR)],
                        dxfattribs={"layer": LAYER_TRENCH_OUT})

        # vertikale Top-Stufe(n) genau auf der/den Naht(en)
        if has_top_step:
            msp.add_lwpolyline([(xSeam,        min(yTopL, yTopR)),
                                (xSeam,        max(yTopL, yTopR))],
                                dxfattribs={"layer": LAYER_TRENCH_OUT})
            if not join_only:
                msp.add_lwpolyline([(xRightStart, min(yTopL, yTopR)),
                                    (xRightStart, max(yTopL, yTopR))],
                                    dxfattribs={"layer": LAYER_TRENCH_OUT})

        # Brücke auf der höheren Oberkante zwischen den Nähten
        if x_right_start_top - x_left_end_top > 1e-9:
            msp.add_lwpolyline([(x_left_end_top,  max(yTopL, yTopR)),
                                (x_right_start_top, max(yTopL, yTopR))],
                            dxfattribs={"layer": LAYER_TRENCH_OUT})

        # falls später genutzt (z. B. linke Nachbar-Naht):
        top_off = 0.0  # x_left_end_top - xSeam == 0 ⇒ keine seitliche Verschiebung

        # --- kleines Top-Zwischenstück an der linken Nachbar-Naht (nur bei Höhenstufe) ---
        if i > 0 and join_L:
            bg0 = trenches[i-1]
            T0_ref, T0_L, T0_R = _depths(bg0)
            base0 = _base_y_with_gok(T0_ref, _gok(bg0))
            yTop0 = base0 + T0_ref
            yTop1 = yTopL

            if abs(yTop0 - yTop1) > 1e-9:
                # Horizontaler Versatz an der linken Stufe (+/- CLR_LR)
                top_off_L = (CLR_LR if (yTop0 <= yTop1 + 1e-9) else -CLR_LR)

                # Start nie links der Außenkante
                x_left_mid_top = max(x_start + top_off_L, xL)

                # Ende STRENG an der Naht und niemals darüber (gegen "Nase")
                x_right_mid_top = min(xSeam, xRightStart) - EPS

                if x_right_mid_top - x_left_mid_top > 1e-9:
                    msp.add_lwpolyline(
                        [(x_left_mid_top, yTopL), (x_right_mid_top, yTopL)],
                        dxfattribs={"layer": LAYER_TRENCH_OUT},
                    )

        # Nur die horizontalen Randbänder außen – die schrägen Linien kommen
        # unten in "LINKE/RECHTE SCHRÄGE" mit sauberem Clipping.
        if not has_pass_left:
            msp.add_lwpolyline([(xL, y_out_L_left), (x_inner_left, y_out_L_left)],
                            dxfattribs={"layer": LAYER_TRENCH_OUT})

        if not has_pass_right:
            msp.add_lwpolyline([(x_inner_right, y_out_R_right), (xR, y_out_R_right)],
                            dxfattribs={"layer": LAYER_TRENCH_OUT})

        # Stufen-Vertikalen an der Lückenkante:
        #  - LINKE KANTE: IMMER zeichnen (auch bei Durchstich) → schließt die Kontur (grüne Linie)
        #  - RECHTE KANTE: nur bei reiner Verbindung, um Doppel-Linien zu vermeiden
        if has_step:
            # linke Lückenkante (an der Naht)
            msp.add_lwpolyline(
                [(x_step_out_L, min(y_out_L_right, y_out_R_left)),
                (x_step_out_L, max(y_out_L_right, y_out_R_left))],
                dxfattribs={"layer": LAYER_TRENCH_OUT}
            )

            # rechte Lückenkante nur ohne Durchstich
            if join_only:
                msp.add_lwpolyline(
                    [(x_step_out_R, min(y_out_L_right, y_out_R_left)),
                    (x_step_out_R, max(y_out_L_right, y_out_R_left))],
                    dxfattribs={"layer": LAYER_TRENCH_OUT}
                )

        cap_left   = 0.0
        step_dir_L = 0.0
        if i > 0 and join_L:
            bg_prev = trenches[i-1]
            Tp_ref, Tp_L, Tp_R = _depths(bg_prev)
            base_p = _base_y_with_gok(Tp_ref, _gok(bg_prev))
            y_out_prev_right = (base_p + (Tp_ref - Tp_R)) - CLR_BOT
            y_out_mid_left   = (baseL  + (T1_ref - T1_L)) - CLR_BOT

            # Stufenrichtung an der linken Naht (nur zur EPS-Logik)
            if abs(y_out_prev_right - y_out_mid_left) > 1e-9:
                step_dir_L = (CLR_LR if (y_out_prev_right <= y_out_mid_left + 1e-9) else -CLR_LR)

            # kappen nur wenn Stufe nach rechts zeigt (sonst Überstand in den Nachbar)
            if step_dir_L > 0:
                cap_left = CLR_LR

        # ---- LINKE SCHRÄGE (BG1) ----------------------------------------------------
        xL0_raw, yL0_raw = x_inner_left, y_out_L_left
        xL1_raw, yL1_raw = x_join_R,    y_out_L_right

        # Linke Stufen-Vertikale (nur relevant, wenn links verbunden ist)
        x_join_L = None
        if i > 0 and join_L and abs(y_out_prev_right - y_out_mid_left) > 1e-9:
            # Stufenrichtung links bereits oben in step_dir_L ermittelt (+CLR_LR / -CLR_LR)
            x_join_L = x_start + step_dir_L  # Vertikale der Außenstufe an der linken Naht

        # Startpunkt bestimmen
        if x_join_L is not None and step_dir_L < 0:  
            # mittlerer BG tiefer -> Stufe nach links: bis zur Stufenvertikalen ziehen
            xL0 = min(x_inner_left - EPS, x_join_L)    # leicht überlappen
        else:
            # wie gehabt: nur kappen, wenn nötig
            eps_left = ( -EPS if (join_L and step_dir_L < 0 and cap_left == 0.0)
                        else (EPS if cap_left > 0 else 0.0) )
            xL0 = xL0_raw + cap_left + eps_left

        # y am neuen Start interpolieren
        def _y_on_line(xa, ya, xb, yb, x):
            if abs(xb - xa) < 1e-12:
                return ya
            t = (x - xa) / (xb - xa)
            return ya + t * (yb - ya)

        yL0 = _y_on_line(xL0_raw, yL0_raw, xL1_raw, yL1_raw, xL0)

        if xL1_raw - xL0 > EPS:
            msp.add_lwpolyline([(xL0, yL0), (xL1_raw, yL1_raw)],
                            dxfattribs={"layer": LAYER_TRENCH_OUT})

        # ---- RECHTE SCHRÄGE (BG2) ---------------------------------------------------
        xR0_raw, yR0_raw = x_join_R,      y_out_R_left
        xR1_raw, yR1_raw = x_inner_right, y_out_R_right

        # Stufe an der aktuellen (rechten) Naht?
        # start_cap_R = (CLR_LR if (join_only and step_dir_R < 0) else 0.0)
        # end_cap_R   = (CLR_LR if (join_only and step_dir_R > 0) else 0.0)

        # Gap-Fix: Überlappung (−EPS/+EPS invertieren)
        xR0_raw, yR0_raw = x_join_R,      y_out_R_left
        xR1_raw, yR1_raw = x_inner_right, y_out_R_right

        # Start exakt an die Stufen-Vertikale kleben, damit keine Lücke entsteht
        if join_only and has_step:
            # Stufen-Vertikale rechts liegt bei x_step_out_R
            xR0 = x_step_out_R - EPS      # kleine Überlappung in die Stufe
            xR1 = xR1_raw                 # Ende wird ggf. unten (Look-ahead) beschnitten
        else:
            xR0 = xR0_raw
            xR1 = xR1_raw

        # --- Look-ahead auf die nächste Naht rechts (BG2 | BG3) wie bisher ---
        if join_R and (i + 2) < len(trenches):
            bg_next = trenches[i + 2]
            Tn_ref, Tn_L, Tn_R = _depths(bg_next)
            base_n = _base_y_with_gok(Tn_ref, _gok(bg_next))
            y_out_next_left = (base_n + (Tn_ref - Tn_L)) - CLR_BOT
            has_step_next   = abs(y_out_R_right - y_out_next_left) > 1e-9
            x_seam_next = xRightStart + L2
            if has_step_next:
                step_dir_next = (CLR_LR if (y_out_R_right <= y_out_next_left + 1e-9) else -CLR_LR)
                x_join_next   = x_seam_next + step_dir_next
                if step_dir_next < 0:
                    xR1 = min(xR1, x_join_next - EPS)
            else:
                xR1 = min(xR1, x_seam_next - EPS)

        # y-Koordinaten zu den ggf. geclippten x berechnen
        def _y_on_line(xa, ya, xb, yb, x):
            if abs(xb - xa) < 1e-12:
                return ya
            t = (x - xa) / (xb - xa)
            return ya + t * (yb - ya)

        yR0 = _y_on_line(xR0_raw, yR0_raw, xR1_raw, yR1_raw, xR0)
        yR1 = _y_on_line(xR0_raw, yR0_raw, xR1_raw, yR1_raw, xR1)

        if xR1 - xR0 > EPS:
            msp.add_lwpolyline([(xR0, yR0), (xR1, yR1)],
                            dxfattribs={"layer": LAYER_TRENCH_OUT})

        # --- Innenbodenlinien MIT Gefälle (bereits vorhanden) ---
        msp.add_lwpolyline([(x_inner_left, y_in_L_left), (xSeam,        y_in_L_right)], dxfattribs={"layer": LAYER_TRENCH_IN})
        msp.add_lwpolyline([(xSeam,        y_in_R_left), (x_inner_right, y_in_R_right)], dxfattribs={"layer": LAYER_TRENCH_IN})

        # Vertikale Innenlinie NUR wenn Innenhöhen an der Naht verschieden sind
        if abs(y_in_L_right - y_in_R_left) > 1e-9:
            y_hi = max(y_in_L_right, y_in_R_left)
            y_lo = min(y_in_L_right, y_in_R_left)
            msp.add_lwpolyline([(xSeam, y_lo), (xSeam, y_hi)], dxfattribs={"layer": LAYER_TRENCH_IN})

        # --- Seitenbänder bis zum Außenboden: füllt die Ecken mit
        if left_clear > 1e-9:
            _hatch_poly([
                (x_start,            y_out_L_left),
                (x_start+left_clear, y_out_L_left),
                (x_start+left_clear, yTopL),
                (x_start,            yTopL),
            ])

        if right_clear > 1e-9:
            _hatch_poly([
                (xR - right_clear, y_out_R_right),
                (xR,               y_out_R_right),
                (xR,               yTopR),
                (xR - right_clear, yTopR),
            ])

        # --- Bodenband links (immer dieses Teilstück)
        _hatch_poly([
            (x_inner_left, y_out_L_left),
            (xSeam,        y_out_L_right),
            (xSeam,        y_in_L_right),
            (x_inner_left, y_in_L_left),
        ])

        # --- Bodenband rechts (nur am rechten Cluster-Ende)
        if not has_pass_right:
            _hatch_poly([
                (xSeam,         y_out_R_left),
                (x_inner_right, y_out_R_right),
                (x_inner_right, y_in_R_right),
                (xSeam,         y_in_R_left),
            ])

        # --- Stufenband an der Naht (füllt die Lücke in der Stufe)
        #     Rechteck zwischen Innen-Stufenvertikaler (xSeam) und der Außenstufe (x_step_out)
        if join_only and has_step:
            x0 = min(xSeam, x_step_out)
            x1 = max(xSeam, x_step_out)
            y_lo = min(y_out_L_right, y_out_R_left)
            y_hi = max(y_out_L_right, y_out_R_left)
            _hatch_poly([(x0, y_lo), (x1, y_lo), (x1, y_hi), (x0, y_hi)])

        if not join_only:
            y_ref = max(yTopL, yTopR)
            # an die Oberkante in der Lücke (Brücke liegt auf max(yTopL, yTopR))
            _draw_pass_symbol_rect(
                xSeam, xRightStart,
                pattern=pas.get("pattern"),
                y_top=max(yTopL, yTopR)
            )

            # Maß für die Durchstich-Länge
            _add_pass_len_dim(xSeam, xRightStart, y_ref)

        # -----------------------------
        # Rohr(e)
        # -----------------------------
        # durchgehendes Rohr über Cluster nur am linken Cluster-Anfang
        if not has_pass_left:
            L_span = 0.0
            last_idx = i
            for idx in range(i, len(trenches)):
                L_span += float(trenches[idx]["length"])
                if idx + 1 < len(trenches):
                    seam = idx + 1
                    if seam in join_set:
                        last_idx = idx + 1
                        continue
                    p_next = _pass_for_between(passes, seam)
                    if p_next is not None:
                        L_span += float(p_next["length"])
                        last_idx = idx + 1
                        continue
                last_idx = idx
                break

            # ein "voll durchgehendes" Rohr aus dem Cluster herausfischen
            pipe_src = None
            for k in range(i, last_idx + 1):
                cand = _first_pipe_for_trench(pipes, k + 1)
                if cand:
                    full, _ = _pipe_full_and_want(cand)
                    if full:
                        pipe_src = cand
                        break

            if pipe_src:
                d   = float(pipe_src.get("diameter", 0) or 0)
                off = float(pipe_src.get("offset", 0) or 0.0)
                if d > 0:
                    # --- Bodenprofil über den ganzen Cluster aufbauen ---
                    profile_segments = []
                    for k in range(i, last_idx + 1):
                        bgk = trenches[k]
                        Lk = float(bgk.get("length", 0) or 0.0)
                        Tk_ref, Tk_L, Tk_R = _depths(bgk)
                        base_k = _base_y_with_gok(Tk_ref, _gok(bgk))
                        y0 = base_k + (Tk_ref - Tk_L)   # Boden links im BG k
                        y1 = base_k + (Tk_ref - Tk_R)   # Boden rechts im BG k
                        if Lk > 1e-9:
                            profile_segments.append((Lk, y0, y1))

                        # ggf. Durchstich zwischen k und k+1 als horizontales Segment
                        if k < last_idx:
                            seam = k + 1
                            if seam not in join_set:
                                p_between = _pass_for_between(passes, seam)
                                if p_between is not None:
                                    pw = float(p_between.get("length", 0) or 0.0)
                                    if pw > 1e-9:
                                        # Boden im Pass = Niveau am rechten Ende des vorherigen BG
                                        profile_segments.append((pw, y1, y1))

                    # --- Zeichnen: EIN durchgehendes Rohr mit Stückprofil ---
                    # x-Referenz ist die linke Innenkante des Clusters
                    eff = draw_pipe_front_piecewise(
                        msp,
                        origin_front=(x_inner_left, 0.0),   # y wird aus dem Profil ermittelt
                        trench_inner_length=L_span,
                        diameter=d,
                        segments=profile_segments,
                        span_length=None,
                        offset=off,
                    )
                    if eff > 0:
                        aufmass.append(
                            f"Rohr {i+1}–{last_idx+1}: l={eff} m  Ø={d} m" + (f"  Versatz={off} m" if off else "")
                        )
                        for k in range(i, last_idx + 1):
                            drawn_pipe.add(k + 1)

        # Einzelrohre links/rechts (falls nötig)
        pipeL = _first_pipe_for_trench(pipes, i+1)
        if pipeL and (i+1) not in drawn_pipe:
            dL = float(pipeL.get("diameter", 0) or 0)
            if dL > 0:
                offL = float(pipeL.get("offset", 0) or 0.0)
                fullL, wantL = _pipe_full_and_want(pipeL)
                effL = draw_pipe_front(
                    msp,
                    origin_front=(x_inner_left, CLR_BOT),
                    trench_inner_length=L1,
                    diameter=dL,
                    span_length=(None if fullL else float(pipeL.get("length"))),
                    offset=offL,
                    bottom_y_left=y_in_L_left,
                    bottom_y_right=y_in_L_right,
                )
                if effL > 0:
                    aufmass.append(f"Rohr {i+1}: l={effL} m  Ø={dL} m" + (f"  Versatz={offL} m" if offL else ""))
                    drawn_pipe.add(i+1)

        pipeR = _first_pipe_for_trench(pipes, i+2)
        if pipeR and (i+2) not in drawn_pipe:
            dR = float(pipeR.get("diameter", 0) or 0)
            if dR > 0:
                offR = float(pipeR.get("offset", 0) or 0.0)
                fullR, wantR = _pipe_full_and_want(pipeR)
                effR = draw_pipe_front(
                    msp,
                    origin_front=(xRightStart, CLR_BOT),
                    trench_inner_length=L2,
                    diameter=dR,
                    span_length=(None if fullR else float(pipeR.get("length"))),
                    offset=offR,
                    bottom_y_left=y_in_R_left,
                    bottom_y_right=y_in_R_right,
                )
                if effR > 0:
                    aufmass.append(f"Rohr {i+2}: l={effR} m  Ø={dR} m" + (f"  Versatz={offR} m" if offR else ""))
                    drawn_pipe.add(i+2)

        # -----------------------------
        # Tiefenmaß links
        # -----------------------------
        if (i+1) not in printed_depth:
            if abs(T1_L - T1_R) < 1e-6:
                # einheitliche Tiefe – Maß außen links
                _add_depth_dim(x_inner_left, yTopL, T1_L, x_inner_left - DIM_OFFSET_FRONT)
            else:
                # Gefälle – zwei Maße, Basislinien nach links versetzen,
                # damit sie sich am xSeam nicht mit den rechten Maßen überlappen
                _add_depth_dim(x_inner_left, yTopL, T1_L, x_inner_left - DIM_OFFSET_FRONT)
                _add_depth_dim(xSeam,        yTopL, T1_R, xSeam        - DIM_OFFSET_FRONT)
            printed_depth.add(i+1)

        # -----------------------------
        # Tiefenmaß rechts
        # -----------------------------
        if (i+2) not in printed_depth:
            if abs(T2_L - T2_R) < 1e-6:
                _add_depth_dim(x_inner_right, yTopR, T2_R, x_inner_right + DIM_OFFSET_FRONT)
            else:
                _add_depth_dim(xSeam,         yTopR, T2_L, xSeam         + DIM_OFFSET_FRONT)
                _add_depth_dim(x_inner_right, yTopR, T2_R, x_inner_right + DIM_OFFSET_FRONT)
            printed_depth.add(i+2)

        # Aufmaß (keine Durchstich-Zeile bei Verbindung)
        if (i+1) not in printed_trench:
            _append_trench_line(aufmass, i+1, L1, B1, T1_ref, T1_L, T1_R); printed_trench.add(i+1)
        if (i+2) not in printed_trench:
            _append_trench_line(aufmass, i+2, L2, B2, T2_ref, T2_L, T2_R); printed_trench.add(i+2)
        if not join_only:
            if (i+1) not in printed_pass:
                aufmass.append(f"Durchstich {i+1}: l={p_w} m")
                printed_pass.add(i+1)

        # Bookkeeping / Cursor / Skip
        trench_origin_x[i]   = x_start
        trench_origin_x[i+1] = xRightStart
        cursor_x = max(cursor_x, x_inner_right + right_clear + GAP_BG)
        skip_single_next = True
        i += 1
        continue

    # ---------- Aufmaß-Block als MText ----------
    # Auto-Aufmaß falls kein manueller Block existiert
    auto_sorted = _sort_aufmass_lines(aufmass)

    # ► Manuelle Zeilen bevorzugen – und REIHENFOLGE BEIBEHALTEN (Drag & Drop)
    manual = _get_manual_aufmass_lines(parsed_json)
    if manual:
        base = [ln.strip() for ln in manual if ln.strip()]
        extras = [ln for ln in auto_sorted if ln.strip() and ln not in base]
        sorted_aufmass = base + extras
    else:
        sorted_aufmass = auto_sorted

    msp.add_mtext(
        "Aufmaß:\n" + "\n".join(sorted_aufmass),
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
    return file_path, "\n".join(sorted_aufmass)

# -----------------------------------------------------
# Edit Element
# -----------------------------------------------------
_BG_IDX_RE = re.compile(r'\b(?:bg|baugraben)\s*([1-9]\d*)\b', re.I)

def _explicit_trench_from_instruction(instr: str) -> int | None:
    m = _BG_IDX_RE.search(instr or "")
    return int(m.group(1)) if m else None

@app.post("/edit-element")
def edit_element(session_id: str, instruction: str = Body(..., embed=True)):
    session = session_manager.get_session(session_id)
    if session is None:
        raise HTTPException(404, "Session unknown")

    prompt = f"""
    Du bist eine JSON-API und darfst AUSSCHLIESSLICH gültiges JSON liefern.

    ANWEISUNG: {instruction!r}

    KONTEXT (Bestand):
    {_build_edit_context(session)}

    ZIEL
    • Bestimme GENAU EIN Zielobjekt und die zu ändernden Felder.
    • Wenn die Anweisung „links“/„rechts“ enthält, ändere NUR das zugehörige Feld:
    – links  → set.depth_left
    – rechts → set.depth_right
    Setze „depth“ NICHT zusätzlich; das System berücksichtigt die größte Tiefe intern.
    • Formulierungen mit „weitere Tiefe“ / „zusätzliche Tiefe“ sind KEINE neuen Objekte,
    sondern ein Feld-Update (z. B. set.depth_right = …).
    • GOK (Geländeoberkante): negative/positive Werte in m sind zulässig.

    BEISPIELE (sehr wichtig)
    Eingabe: "Füge zum ersten Baugraben eine weitere Tiefe rechts mit 1,50 m hinzu."
    Antwort:
    {{
    "selection": {{ "type": "Baugraben", "trench_index": 1 }},
    "set": {{ "depth_right": 1.5 }},
    "answer": "Tiefe rechts bei Baugraben 1 auf 1,50 m gesetzt."
    }}
    Eingabe: "Ändere bei Bg3 GOK auf -0,3"
    Antwort:
    {{
    "selection": {{ "type": "Baugraben", "trench_index": 3 }},
    "set": {{ "gok": -0.3 }},
    "answer": "GOK bei Baugraben 3 auf -0,30 m gesetzt."
    }}
    Eingabe: "Setze bei Bg2 GOK auf -0,30 m"
    Antwort:
    {{
    "selection": {{ "type": "Baugraben", "trench_index": 2 }},
    "set": {{ "gok": -0.30 }},
    "answer": "GOK bei Baugraben 2 auf -0,30 m gesetzt."
    }}

    ADRESSIERUNG
    • Typen: "Baugraben" | "Rohr" | "Oberflächenbefestigung" | "Durchstich".
    • Baugraben N        → selection.trench_index = N (1-basiert).
    • Rohr im/zu Baugraben N → selection.for_trench = N.
    • Oberflächenbefestigung im/zu Baugraben N → selection.for_trench=N, optional selection.seq=M.
    • Durchstich zwischen Baugraben N und N+1 → selection.between = N.
    • Synonyme verstehen: „BG“, „Graben“, „Druckrohr“, „Oberfläche“, „Gehwegplatten“, „Pflaster“, etc.

    FALLBACKS
    • Wenn die Anweisung keinen Index nennt und genau EIN passendes Objekt existiert,
    adressiere dieses.
    • Wenn mehrere existieren und kein Index genannt wird, wähle das zuletzt angelegte.

    WAS NICHT TUN
    • KEINE neuen Elemente hinzufügen oder löschen.
    • KEIN 'trench_index' bei Nicht-Baugraben setzen.

    ERLAUBTE ÄNDERUNGEN
    length | width | depth | depth_left | depth_right | gok | diameter | material | offset | pattern
    • „DN300“ o. ä. → diameter = 0.30 (Meter).
    • Komma-/Punktwerte und Einheiten mm/cm/m korrekt interpretieren.

    ANTWORT (exakt):
    {{
    "selection": {{
        "type": "Baugraben | Rohr | Oberflächenbefestigung | Durchstich",
        "trench_index": 0,
        "for_trench": 0,
        "seq": 0,
        "between": 0,
        "ordinal": 0
    }},
    "set": {{}},
    "answer": "kurz auf Deutsch"
    }}
    """

    try:
        response = client.chat.completions.create(
            model="qwen/qwen3-coder",
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": "You are a JSON API."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=400,
            temperature=0.0,
        )
        data = json.loads(response.choices[0].message.content)
    except Exception as e:
        raise HTTPException(500, f"Fehler ChatGPT: {e}")

    sel = data.get("selection") or {}
    updates_raw = data.get("set") or {}
    if not isinstance(sel, dict) or not sel.get("type"):
        raise HTTPException(400, "Ungültige LLM-Antwort: selection fehlt/leer.")

    # 1) Normalisieren
    sel["type"] = _normalize_type_aliases(sel["type"])
    updates = _coerce_updates(updates_raw)

    explicit_bg = _explicit_trench_from_instruction(instruction)
    if explicit_bg is not None and sel.get("type","").lower().startswith("baugraben"):
        sel["trench_index"] = explicit_bg

    if ("gok" not in updates
        and sel.get("type","").lower().startswith("baugraben")
        and re.search(r"\b(lösch(e)?|entfern(e)?|reset(te)?)\b.*\bgok\b", instruction.lower())):
        updates["gok"] = 0.0

    # 2) Ziel finden (LLM-Auswahl → Backend-Heuristik als Fallback)
    elems = session.setdefault("elements", [])
    count_trenches = sum(1 for e in elems if (e.get("type","") or "").lower().startswith("baugraben"))

    # Harte Validierung: es muss existieren
    if sel.get("type","").lower().startswith("baugraben"):
        ti = int(sel.get("trench_index") or 0)
        if ti < 1 or ti > count_trenches:
            raise HTTPException(404, f"Baugraben {ti} existiert nicht (1..{count_trenches}).")

    idx = _find_target_index_by_selection(elems, sel)
    if idx is None:
        idx = _resolve_selection_heuristic(elems, sel)
    if idx is None:
        raise HTTPException(404, f"Zielobjekt nicht gefunden für selection={sel}")

    # 3) Patch anwenden
    _apply_update(elems[idx], updates)

    # 4) Normalisieren + speichern
    _normalize_and_reindex(session)
    session_manager.update_session(session_id, session)

    return {
        "status": "ok",
        "updated_json": session,
        "answer": data.get("answer", "")
    }

# -----------------------------------------------------
# Delete Element (robust, single + bulk)
# -----------------------------------------------------
@app.post("/remove-element")
def remove_element(session_id: str, instruction: str = Body(..., embed=True)):
    session = session_manager.get_session(session_id)
    if session is None:
        raise HTTPException(404, "Session unknown")

    prompt = f"""
Du bist eine JSON-API und gibst AUSSCHLIESSLICH gültiges JSON zurück.

ANWEISUNG: {instruction!r}

KONTEXT (Bestand – komprimiert):
{_build_edit_context(session)}

ZIEL
• Bestimme, welches Objekt (oder welche Menge) zu löschen ist.
• Nutze Synonyme: „BG“/„Graben“ → Baugraben, „Druckrohr“/„Leitung“ → Rohr,
  „Oberfläche“/„Pflaster“/„Gehwegplatten“ → Oberflächenbefestigung.
  „Verbindung/verbinde/Verbund/connect“ → Verbindung.

SONDERFALL
• Wenn der Text das Löschen/Entfernen/Zurücksetzen von „GOK“ bei einem Baugraben verlangt
  (z. B. „Lösche GOK bei Bg2“, „entferne die Geländeoberkante bei BG 1“),
  ERZEUGE KEINE LÖSCHUNG. Gib stattdessen:
  - selection.type = "Baugraben", selection.trench_index = N
  - mode = "reset_gok"
  (Die Logik setzt GOK intern auf 0,00 m zurück.)

ADRESSIERUNG
• "Baugraben N"             → selection.trench_index = N
• "Rohr im/zu BG N"         → selection.for_trench = N
• "Oberfläche in BG N"      → selection.for_trench = N, optional selection.seq = M
• "Durchstich zw. BG N & N+1" → selection.between = N
• "Verbindung zw. BG N & N+1" → selection.type = "Verbindung", selection.between = N
• Wenn kein Index genannt und genau EIN passendes Objekt existiert → dieses.
• Wenn mehrere existieren und kein Index → das zuletzt angelegte (Fallback).

MODUS
• Wenn der Text eindeutig „alle“, „sämtliche“, „komplett“ enthält
  (z. B. „Lösche alle Oberflächen in BG 2“, „Entferne alle Durchstiche“, „Lösche alle Verbindungen“),
  setze "mode": "bulk".
• Sonst "mode": "single".
• Für den SONDERFALL oben verwende "mode": "reset_gok".

BEISPIELE
Eingabe: "Lösche GOK bei Bg2"
Antwort:
{{
  "selection": {{ "type": "Baugraben", "trench_index": 2 }},
  "mode": "reset_gok",
  "answer": "GOK bei Baugraben 2 auf 0,00 m zurückgesetzt."
}}

ANTWORTFORMAT (exakt so!):
{{
  "selection": {{
    "type": "Baugraben | Rohr | Oberflächenbefestigung | Durchstich | Verbindung",
    "trench_index": 0,
    "for_trench": 0,
    "seq": 0,
    "between": 0,
    "ordinal": 0
  }},
  "mode": "single" | "bulk",
  "answer": "kurz auf Deutsch"
}}
"""

    try:
        response = client.chat.completions.create(
            model="qwen/qwen3-coder",
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": "You are a JSON API."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=400,
            temperature=0.0,
        )
        data = json.loads(response.choices[0].message.content)
    except Exception as e:
        raise HTTPException(500, f"Fehler ChatGPT: {e}")

    sel = data.get("selection") or {}
    if not isinstance(sel, dict) or not sel.get("type"):
        raise HTTPException(400, "Ungültige LLM-Antwort: selection fehlt/leer.")

    # 1) Normalisieren
    sel["type"] = _normalize_type_aliases(sel["type"])
    mode = (data.get("mode") or "single").lower()
    if mode not in ("single", "bulk", "reset_gok"):
        mode = "single"

    elems = session.setdefault("elements", [])

    # Hilfsfilter: passt Element zu selection?
    def _matches_bulk(e: dict, s: dict) -> bool:
        et = (e.get("type","") or "").lower()
        if et in ("aufmass", "aufmass_override"):  # nie löschen
            return False

        t = (s.get("type","") or "").lower()

        if "baugraben" in t:
            if "baugraben" not in et: return False
            ti = s.get("trench_index", None)
            return (ti is None) or (int(e.get("trench_index",0)) == int(ti))

        if "rohr" in t:
            if "rohr" not in et: return False
            ft = s.get("for_trench", None)
            return (ft is None) or (int(e.get("for_trench",0)) == int(ft))

        if ("oberflächenbefest" in t) or ("oberflaechenbefest" in t):
            if ("oberflächenbefest" not in et) and ("oberflaechenbefest" not in et):
                return False
            ft = s.get("for_trench", None)
            seq = s.get("seq", None)
            ok = True
            if ft is not None: ok &= int(e.get("for_trench",0)) == int(ft)
            if seq is not None: ok &= int(e.get("seq",0) or 0) == int(seq)
            return ok

        if "durchstich" in t:
            if "durchstich" not in et: return False
            b = s.get("between", None)
            # „ordinal“ verwenden wir nur im single-Modus; für bulk ist es egal.
            return (b is None) or (int(e.get("between", -1)) == int(b))

        if "verbindung" in t:
            if "verbindung" not in et: return False
            b = s.get("between", None)
            return (b is None) or (int(e.get("between", -1)) == int(b))

        if mode != "reset_gok" and re.search(r"\b(lösch(e|en)?|entfern(e|en)?|reset(te|ten)?|zurücksetz(e|en)?)\b.*\b(gok|gel[äa]ndeoberkante)\b", instruction, re.I):
            mode = "reset_gok"
            m = re.search(r"\b(?:bg|baugraben)\s*([1-9]\d*)\b", instruction, re.I)
            if m:
                sel["trench_index"] = int(m.group(1))
                sel["type"] = "Baugraben"

        return False

    deleted = 0

    # 2) Löschen
    # --- Aktion: reset_gok VOR den Löschzweigen behandeln ---
    if mode == "reset_gok":
        idx = _find_target_index_by_selection(elems, sel)
        if idx is None:
            idx = _resolve_selection_heuristic(elems, sel)
        if idx is None:
            raise HTTPException(404, f"Zielobjekt nicht gefunden für selection={sel}")

        if (elems[idx].get("type","") or "").lower().startswith("baugraben"):
            elems[idx]["gok"] = 0.0
        else:
            raise HTTPException(400, "GOK-Reset ist nur für Baugräben zulässig.")

    # --- 2) Löschen ---
    elif mode == "single":
        idx = _find_target_index_by_selection(elems, sel)
        if idx is None:
            idx = _resolve_selection_heuristic(elems, sel)
        if idx is None:
            raise HTTPException(404, f"Zielobjekt nicht gefunden für selection={sel}")

        t_low = (elems[idx].get("type","") or "").lower()
        if t_low in ("aufmass", "aufmass_override"):
            raise HTTPException(400, "Dieses Element ist nicht löschbar.")

        del elems[idx]
        deleted = 1

    elif mode == "bulk":
        to_delete = [i for i, e in enumerate(elems) if _matches_bulk(e, sel)]
        if not to_delete:
            raise HTTPException(404, f"Keine passenden Elemente für bulk selection={sel} gefunden.")
        for i in reversed(to_delete):
            del elems[i]
        deleted = len(to_delete)

    # 3) Normalisieren + speichern
    _normalize_and_reindex(session)
    session_manager.update_session(session_id, session)

    # Hinweis: Antwort vom LLM ist rein „sprachlich“
    return {
        "status": "ok",
        "deleted": deleted,

        "updated_json": session,
        "answer": data.get("answer", "")
    }

# -----------------------------------------------------
# Generate Invoice
# -----------------------------------------------------
@app.post("/invoice")
def build_invoice(req: InvoiceRequest):
    file = f"temp/invoice_{uuid.uuid4()}.pdf"
    make_invoice(file, company="Muster GmbH", mapping=req.mapping)
    return FileResponse(file, media_type="application/pdf",
                        filename="Rechnung.pdf")