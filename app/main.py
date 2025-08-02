import os
from dotenv import load_dotenv
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = BASE_DIR / '.env' / '.env'

print("Loading .env from:", ENV_FILE)
load_dotenv(ENV_FILE)

from fastapi import FastAPI 
from fastapi.middleware.cors import CORSMiddleware
from app.routes import session_routes, element_routes, dxf_routes, billing_routes

def create_app() -> FastAPI:
    app = FastAPI()
    
    # CORS setup
    allowed_origins = os.getenv("ALLOWED_ORIGINS")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    
    # Include routers
    app.include_router(session_routes.router, tags=["Session"])
    app.include_router(element_routes.router, tags=["Elements"])
    app.include_router(dxf_routes.router, tags=["DXF"])
    app.include_router(billing_routes.router, tags=["Billing"])
    
    return app

app = create_app()
