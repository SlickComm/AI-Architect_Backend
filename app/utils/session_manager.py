from fastapi import HTTPException
import uuid
import threading
import time

class SessionManager:
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
            
        self.session_data = {}
        self.logger_thread = None
        self.is_logging = True
        print("Session Manager initialized (Singleton).")
        self.start_session_logger()
        self._initialized = True
    
    def session_logger(self):
        while self.is_logging:
            if self.session_data:  # Nur loggen wenn es auch Sessions gibt
                print("\nCurrent sessions:", self.session_data)
            time.sleep(5)
    
    def start_session_logger(self):
        if not self.logger_thread or not self.logger_thread.is_alive():
            self.logger_thread = threading.Thread(target=self.session_logger, daemon=True)
            self.logger_thread.start()
    
    def stop_session_logger(self):
        self.is_logging = False
        if self.logger_thread:
            self.logger_thread.join()
    
    def create_session(self):
        new_session_id = str(uuid.uuid4())
        self.session_data[new_session_id] = {"elements": []}
        return {"session_id": new_session_id}
    
    def get_session(self, session_id: str):
        if session_id not in self.session_data:
            raise HTTPException(status_code=400, detail="Session not found.")
        return self.session_data[session_id]
    
    def update_session(self, session_id: str, new_data: dict):
        self.session_data[session_id] = new_data
        
