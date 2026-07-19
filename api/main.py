"""FastAPI application entry point (PLAN section 7).

    uvicorn api.main:app --reload --port 8000

Caches (prediction model, cross-division offsets, per-fighter career
aggregates, search index) are built once on startup into :data:`api.state.state`.
The database is read-only from the API's perspective, so those caches only go
stale when ``ufc.db`` is rebuilt offline -- call ``POST /api/refresh`` (or
restart) to reload them.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api.routers import events, fighters, matchup, rankings
from api.state import state


@asynccontextmanager
async def lifespan(app: FastAPI):
    state.load()  # build all caches before serving the first request
    yield


app = FastAPI(
    title="UFC Elo API",
    version="1.0.0",
    description="Rankings, fighter profiles, upcoming cards and matchup "
                "predictions for the UFC Elo project.",
    lifespan=lifespan,
)

# CORS: open for local Vite / CRA dev servers (PLAN section 8 frontend).
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1):\d+",
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)

app.include_router(rankings.router, prefix="/api", tags=["rankings"])
app.include_router(fighters.router, prefix="/api", tags=["fighters"])
app.include_router(events.router, prefix="/api", tags=["events"])
app.include_router(matchup.router, prefix="/api", tags=["matchup"])


@app.get("/api/health", tags=["meta"])
def health() -> dict[str, Any]:
    """Liveness + cache-population summary."""
    return {
        "status": "ok",
        "caches_loaded": state.loaded,
        "career_fighters_cached": len(state.career),
        "search_index_size": len(state.search_index),
    }


@app.post("/api/refresh", tags=["meta"])
def refresh() -> dict[str, Any]:
    """Rebuild every in-memory cache from the current ``ufc.db`` (see module doc)."""
    state.load()
    return {
        "status": "refreshed",
        "career_fighters_cached": len(state.career),
        "search_index_size": len(state.search_index),
    }


# Ensure the 422 contract (bad params) is JSON-consistent; FastAPI already
# returns 422 for validation errors, this just normalizes the envelope.
@app.exception_handler(404)
async def not_found_handler(request: Request, exc) -> JSONResponse:
    detail = getattr(exc, "detail", "not found")
    return JSONResponse(status_code=404, content={"error": detail})
