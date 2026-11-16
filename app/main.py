from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app import db
from app.bootstrap import bootstrap
from app.settings import settings


ROOT = Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    if settings.env == "dev" and os.environ.get("DG_AUTO_BOOTSTRAP", "1") == "1":
        bootstrap()
    yield


app = FastAPI(title="Deutsch Grader", version="0.1.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=ROOT / "web"), name="static")


@app.middleware("http")
async def security_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'"
    )
    return response


@app.get("/healthz")
def healthz() -> dict:
    conn = db.connect()
    try:
        conn.execute("SELECT 1")
    finally:
        conn.close()
    return {"ok": True}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(ROOT / "web" / "index.html")


from app.api.routes import router  # noqa: E402

app.include_router(router)
