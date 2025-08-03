from fastapi import HTTPException
import uuid
import threading
import time

class SessionManager:
    _store: dict[str, dict] = {} 

    def create_session(self):
        sid = str(uuid.uuid4())
        self._store[sid] = {"elements": []}
        return {"session_id": sid}

    def get_session(self, sid: str):
        return self._store.setdefault(sid, {"elements": []})

    def update_session(self, sid: str, data: dict):
        self._store[sid] = data

session_manager = SessionManager()