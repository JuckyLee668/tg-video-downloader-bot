import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from core.config import config
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
    
    # Mount thumbnails static directory
    thumb_dir = os.path.expanduser(config.save_path.rstrip("/downloads")) + "/../.tg_downloader_thumbs"
    thumb_dir_real = os.path.join(os.path.dirname(os.path.abspath(".")), ".tg_downloader_thumbs")
    if os.path.exists("/root/.tg_downloader_thumbs"):
        app.mount("/thumbs", StaticFiles(directory="/root/.tg_downloader_thumbs"), name="thumbs")
    elif os.path.exists(thumb_dir):
        app.mount("/thumbs", StaticFiles(directory=thumb_dir), name="thumbs")
    
    # Static files (the existing dashboard.html)
    # Check if public directory exists
    if os.path.exists("public"):
        app.mount("/", StaticFiles(directory="public", html=True), name="static")
    
    return app
