from fastapi import APIRouter, Body

from app.services.openai_service import OpenAIService
from app.utils.session_manager import SessionManager

router = APIRouter()
session_manager = SessionManager()
openai_service = OpenAIService()

@router.post("/add-element/")
def add_element(session_id: str, description: str = Body(..., embed=True)):
    print("Add element")
    print(session_id)
    session = session_manager.get_session(session_id)
    reply = openai_service.add_element(session, description)
    session_manager.update_session(session_id, reply["updated_json"])
    return reply

@router.post("/edit-element/")
def edit_element(session_id: str, instruction: str = Body(..., embed=True)):
    session = session_manager.get_session(session_id)
    return openai_service.edit_element(session, instruction)

@router.post("/remove-element/")
def remove_element(session_id: str, instruction: str = Body(..., embed=True)):
    session = session_manager.get_session(session_id)
    return openai_service.remove_element(session, instruction)
