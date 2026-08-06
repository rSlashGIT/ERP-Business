"""ERP API entrypoint."""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .api.v1 import dashboard, inventory, procurement, sales
from .clients.smartstock import SmartStockClient

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("erp")


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.smartstock = SmartStockClient(
        base_url=os.getenv("SMARTSTOCK_URL", "http://smartstock:8100"),
        api_key=os.getenv("SMARTSTOCK_API_KEY", ""),
    )
    try:
        health = await app.state.smartstock.health()
        logger.info("SmartStock reachable: %s", health)
    except Exception as exc:
        # Do NOT fail startup. The ERP is useful without the AI service; it
        # simply cannot generate recommendations until SmartStock recovers.
        logger.warning("SmartStock unreachable at startup: %s", exc)
    yield
    await app.state.smartstock.aclose()


app = FastAPI(title="ERP API", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:5173").split(","),
    allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
)
app.include_router(inventory.router)
app.include_router(procurement.router)
app.include_router(dashboard.router)
app.include_router(sales.router)


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


@app.get("/readyz")
async def readyz():
    from sqlalchemy import text
    from .db.session import engine
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        db_ok = True
    except Exception as exc:
        logger.error("database not ready: %s", exc)
        db_ok = False
    try:
        await app.state.smartstock.health()
        ai_ok = True
    except Exception:
        ai_ok = False
    return JSONResponse(
        status_code=200 if db_ok else 503,
        content={"database": db_ok, "smartstock": ai_ok, "degraded": not ai_ok},
    )
