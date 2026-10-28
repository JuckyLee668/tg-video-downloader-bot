from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from core.config import config
from telegram.handlers.thumbnail import THUMB_DIR
from web.qr_login import public_router as qr_public_router
from web.routes import router as api_router


def create_app():
    app = FastAPI(title="TG Media Downloader Dashboard")
    cors_origins = config.web_cors_origins or ["http://127.0.0.1:8000"]

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # API Routes
    app.include_router(api_router, prefix="/api")
    # Token-guarded QR login page (no API key: browsers cannot send headers on <img>)
    app.include_router(qr_public_router, prefix="/api/login/qr")

    # Thumbnails are created lazily at runtime, so make sure the directory
    # exists before mounting (otherwise the route is missing until a restart).
    THUMB_DIR.mkdir(parents=True, exist_ok=True)
    app.mount("/thumbs", StaticFiles(directory=str(THUMB_DIR)), name="thumbs")

    # Static files.  Resolve relative to the project root so bundled builds
    # work when the process is started from another working directory.
    public_dir = Path(__file__).resolve().parent.parent / "public"
    if not public_dir.exists():
        public_dir = Path("public")
    if public_dir.exists():
        app.mount("/", StaticFiles(directory=str(public_dir), html=True), name="static")

    return app
