# app/main.py
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Router-Module
from app.routes import (
    session_routes,
    element_routes,
    dxf_routes,
    billing_routes,
    payment_routes,
)

# -------------------------------------------------------------------------
# .env laden – liegt bei dir unter <repo>/.env/.env
# -------------------------------------------------------------------------
BASE_DIR  = Path(__file__).resolve().parent.parent
ENV_FILE  = BASE_DIR / ".env" / ".env"
print("Loading .env from:", ENV_FILE)
load_dotenv(ENV_FILE)            # setzt z. B. OPENAI_API_KEY, ALLOWED_ORIGINS …

# -------------------------------------------------------------------------
# App-Factory
# -------------------------------------------------------------------------
def create_app() -> FastAPI:
    app = FastAPI(
        title="ChatCAD Backend",
        version="1.0.0",
        docs_url="/docs",            # Swagger-UI
        redoc_url=None,
    )

    # -------------------------- CORS -------------------------------------
    allowed_origins = (
        os.getenv("ALLOWED_ORIGINS", "*")
        .replace(" ", "")
        .split(",")
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ------------------------- Router ------------------------------------
    # Alle Routen werden darüber registriert – KEINE eigenen Endpunkte
    app.include_router(session_routes.router, tags=["Session"])
    app.include_router(element_routes.router, tags=["Elemente"])
    app.include_router(dxf_routes.router,     tags=["DXF"])
    app.include_router(billing_routes.router, tags=["Rechnung"])
    app.include_router(payment_routes.router, tags=["Payment"])

    return app


# -------------------------------------------------------------------------
# FastAPI-Instanz, die von uvicorn / gunicorn gepickt wird
# -------------------------------------------------------------------------
app = create_app()
