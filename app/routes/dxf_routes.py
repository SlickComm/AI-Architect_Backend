from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
import os, pathlib, uuid

from app.utils.session_manager import session_manager    
from app.services.dxf_service  import DXFService

router      = APIRouter()
dxf_service = DXFService()

@router.post("/generate-dxf-by-session")
def generate_dxf_by_session(session_id: str = Query(...)):
    """
    Erzeugt eine DXF-Datei aus der aktuellen Session
    und hängt den Aufmaß-Block in `session_manager` an.
    """
    session = session_manager.get_session(session_id)
    if not session["elements"]:
        raise HTTPException(400, "Session leer – erst Elemente anlegen")

    try:
        dxf_path, aufmass_txt = dxf_service.generate_dxf(session)

        session["elements"].append({
            "type": "aufmass",
            "text": aufmass_txt
        })
        session_manager.update_session(session_id, session)

        return FileResponse(
            dxf_path,
            media_type = "application/dxf",
            filename   = os.path.basename(dxf_path),
        )

    except Exception as exc:
        raise HTTPException(500, f"DXF-Erstellung fehlgeschlagen: {exc}")
