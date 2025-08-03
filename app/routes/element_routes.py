# app/routes/element_routes.py
from fastapi import APIRouter, Body, Query, HTTPException

from app.services.openai_service import OpenAIService
from app.utils.session_manager  import session_manager  # <- dieselbe Instanz!

router         = APIRouter()
openai_service = OpenAIService()

# ────────────────────────────────────────────────────────────────
# /add-element
# ────────────────────────────────────────────────────────────────
@router.post("/add-element")                     
def add_element(
    session_id  : str = Query(...),
    description : str = Body(..., embed=True)
):
    session = session_manager.get_session(session_id)
    if not session:
        raise HTTPException(404, "Session unknown")

    reply = openai_service.add_element(session, description)
    session_manager.update_session(session_id, reply["updated_json"])
    return reply

# ────────────────────────────────────────────────────────────────
# /edit-element
# ────────────────────────────────────────────────────────────────
@router.post("/edit-element")
def edit_element(
    session_id  : str = Query(...),
    instruction : str = Body(..., embed=True)
):
    session = session_manager.get_session(session_id)
    if not session:
        raise HTTPException(404, "Session unknown")

    reply = openai_service.edit_element(session, instruction)
    session_manager.update_session(session_id, reply["updated_json"])
    return reply

# ────────────────────────────────────────────────────────────────
# /remove-element
# ────────────────────────────────────────────────────────────────
@router.post("/remove-element")
def remove_element(
    session_id  : str = Query(...),
    instruction : str = Body(..., embed=True)
):
    session = session_manager.get_session(session_id)
    if not session:
        raise HTTPException(404, "Session unknown")

    reply = openai_service.remove_element(session, instruction)
    session_manager.update_session(session_id, reply["updated_json"])
    return reply
