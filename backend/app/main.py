import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api import alerts, auth, backtests, etfs, portfolio, scans, stocks
from app.config import get_settings
from app.db import init_db
from app.security import require_csrf_header
from app.services.scheduler import scheduler_loop

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
FRONT_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
CSP = ("default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
       "object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
DOC_PATHS = ("/api/docs", "/api/openapi.json")


@asynccontextmanager
async def lifespan(_: FastAPI):
    get_settings().signing_key()  # échoue tôt si la clé manque en production
    init_db()
    task = asyncio.create_task(scheduler_loop()) if get_settings().scan_schedule else None
    yield
    if task:
        task.cancel()


def create_app() -> FastAPI:
    s = get_settings()
    dev = s.environment == "dev"
    app = FastAPI(
        title="PEA Radar", version="0.1.0", lifespan=lifespan,
        docs_url="/api/docs" if dev else None, redoc_url=None,
        openapi_url="/api/openapi.json" if dev else None,
    )
    app.add_middleware(
        CORSMiddleware, allow_origins=s.cors_origins, allow_credentials=True,
        allow_methods=["GET", "POST"], allow_headers=["Content-Type", "X-Requested-With"],
    )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        h = response.headers
        h["X-Content-Type-Options"] = "nosniff"
        h["X-Frame-Options"] = "DENY"
        h["Referrer-Policy"] = "no-referrer"
        h["Permissions-Policy"] = "geolocation=(), camera=(), microphone=()"
        if not request.url.path.startswith(DOC_PATHS):
            h["Content-Security-Policy"] = CSP
        if request.url.path.startswith("/api"):
            h["Cache-Control"] = "no-store"
        if s.cookie_secure:
            h["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response

    api = APIRouter(prefix="/api", dependencies=[Depends(require_csrf_header)])
    for module in (auth, scans, stocks, portfolio, backtests, alerts, etfs):
        api.include_router(module.router)

    @api.get("/health", tags=["system"])
    def health() -> dict:
        return {"status": "ok"}

    app.include_router(api)

    if FRONT_DIST.is_dir():  # en production, l'API sert aussi le front compilé
        app.mount("/assets", StaticFiles(directory=FRONT_DIST / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str) -> FileResponse:
            if path.startswith("api/"):
                raise HTTPException(404, "Route inconnue")
            return FileResponse(FRONT_DIST / "index.html")

    return app


app = create_app()
