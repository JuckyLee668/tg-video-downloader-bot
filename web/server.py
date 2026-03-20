from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from web.routes import router as api_router
from utils.runtime_paths import bundle_path

def create_app():
    app = FastAPI(title="TG Media Downloader Dashboard")
    
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    
    # API Routes
    app.include_router(api_router, prefix="/api")
    
    # Static files (the existing dashboard.html)
    # Check if public directory exists
    public_dir = bundle_path("public")
    if public_dir.exists():
        app.mount("/", StaticFiles(directory=str(public_dir), html=True), name="static")
    
    return app
