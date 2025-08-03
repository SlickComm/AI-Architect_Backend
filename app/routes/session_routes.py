from fastapi import APIRouter

from app.utils.session_manager import session_manager

router = APIRouter()

@router.post("/start-session")     
def start_session():
    return session_manager.create_session()
