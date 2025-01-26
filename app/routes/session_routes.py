from fastapi import APIRouter

from app.utils.session_manager import SessionManager

router = APIRouter()
session_manager = SessionManager()

@router.post("/start-session/")
def start_session():
    return session_manager.create_session()
