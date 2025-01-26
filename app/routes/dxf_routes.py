from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
import os
from ..services.dxf_service import DXFService
from ..utils.session_manager import SessionManager

router = APIRouter()
session_manager = SessionManager()
dxf_service = DXFService()

@router.post("/generate-dxf-by-session/")
def generate_dxf_by_session(session_id: str):
    current_session = session_manager.get_session(session_id)
    try:
        dxf_file_path = dxf_service.generate_dxf(current_session)
        return FileResponse(
            dxf_file_path,
            media_type="application/dxf",
            filename=os.path.basename(dxf_file_path),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
