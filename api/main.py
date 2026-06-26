from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from api.routers import analysis, observability, research, risk, selection, signals, strategies
from config.strategy_loader import load_all_strategies
from db.connection import close_db, init_db
from scheduler.setup import start_scheduler, stop_scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    loaded = load_all_strategies(Path(__file__).resolve().parents[1] / "strategies")
    app.state.strategy_map = {strategy.strategy_id: strategy for strategy in loaded}
    start_scheduler(app)
    try:
        yield
    finally:
        stop_scheduler(app)
        await close_db()


app = FastAPI(title="EvoTraders Quant Platform", lifespan=lifespan)


@app.middleware("http")
async def api_token_auth(request: Request, call_next):
    token = os.getenv("API_TOKEN", "").strip()
    if token and request.url.path.startswith("/api/"):
        provided = request.headers.get("x-api-key", "")
        if provided != token:
            return JSONResponse(
                status_code=401,
                content={"detail": "invalid or missing x-api-key"},
            )
    return await call_next(request)


app.include_router(analysis.router)
app.include_router(observability.router)
app.include_router(research.router)
app.include_router(selection.router)
app.include_router(signals.router)
app.include_router(strategies.router)
app.include_router(risk.router)


@app.get("/health")
async def health():
    return {"status": "ok"}
