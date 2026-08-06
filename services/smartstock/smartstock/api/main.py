"""
SmartStock HTTP service (FastAPI).

UPGRADE 4 OF 4: REAL-TIME API
-----------------------------
Legacy SmartStock was a batch pipeline: read CSVs from data/processed/, write
JSON into frontend/data/, and have a static page read the files. There was no
way for another system to ask it a question. Now:

    POST /v1/recommendations:generate   synchronous, batched, <100ms for 1k SKUs
    POST /v1/policy:optimize            async CMA-ES refit, returns a job handle
    GET  /v1/jobs/{job_id}              poll fit progress
    GET  /v1/policy                     inspect fitted parameters (explainability)
    PUT  /v1/policy                     hot-swap parameters without a restart
    POST /v1/simulate                   what-if against a candidate policy
    GET  /healthz  /readyz  /metrics    ops

CONCURRENCY MODEL
-----------------
Recommendation generation is CPU-bound NumPy that releases the GIL in the hot
loops. It runs in a threadpool via `run_in_threadpool` so a slow 50k-line
request cannot block the event loop and starve health checks.

CMA-ES fits are minutes long, so they run in a ProcessPoolExecutor and are
addressed by job id. Never inline a fit into a request handler: the ERP's
nightly Celery task has a timeout and will retry, and you will end up running
four concurrent fits of the same catalogue.
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from typing import Dict, List, Optional

import numpy as np
from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse

from ..config import settings
from ..contracts import (
    HealthResponse,
    JobHandle,
    JobStatus,
    OptimizeRequest,
    ReplenishmentRequest,
    ReplenishmentResponse,
)
from ..core.recommend import ENGINE_VERSION, PolicyStore, generate
from ..training.jobs import JobRegistry, run_fit_job

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("smartstock.api")

app = FastAPI(
    title="SmartStock",
    version=ENGINE_VERSION,
    description="Inventory optimisation engine: continuous (s,S) under stochastic lead times.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("SMARTSTOCK_CORS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)

STORE = PolicyStore()
JOBS = JobRegistry(ttl_seconds=settings.job_ttl_seconds)
EXECUTOR: Optional[ProcessPoolExecutor] = None
_STARTED = time.time()
_METRICS: Dict[str, float] = {
    "requests_total": 0, "lines_generated": 0, "errors_total": 0, "fit_jobs_total": 0,
    "generate_seconds_total": 0.0,
}


# ─────────────── lifecycle ───────────────

@app.on_event("startup")
def _startup() -> None:
    global EXECUTOR
    EXECUTOR = ProcessPoolExecutor(max_workers=int(os.getenv("SMARTSTOCK_FIT_WORKERS", "2")))
    path = settings.policy_path
    if os.path.exists(path):
        try:
            import json
            with open(path) as fh:
                blob = json.load(fh)
            STORE.load(blob["params"], blob.get("version"))
            logger.info("loaded policy %s with %d segments", STORE.policy_version, len(STORE.segments))
        except Exception:
            logger.exception("failed to load policy from %s; serving classical default", path)
    else:
        logger.warning("no policy at %s; serving classical (s,S) default", path)


@app.on_event("shutdown")
def _shutdown() -> None:
    if EXECUTOR is not None:
        EXECUTOR.shutdown(wait=False, cancel_futures=True)


# ─────────────── auth ───────────────

async def require_key(x_api_key: Optional[str] = Header(default=None)) -> None:
    """Shared-secret auth. Empty SMARTSTOCK_API_KEY disables it for local dev.

    SmartStock is an internal service and must never be internet-facing; this
    is defence in depth for the case where someone exposes the pod anyway.
    """
    if not settings.api_key:
        return
    if x_api_key != settings.api_key:
        raise HTTPException(status_code=401, detail="invalid or missing X-API-Key")


# ─────────────── errors ───────────────

@app.exception_handler(Exception)
async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
    _METRICS["errors_total"] += 1
    logger.exception("unhandled error on %s", request.url.path)
    return JSONResponse(
        status_code=500,
        content={"error": "internal_error", "detail": str(exc)[:500]},
    )


# ─────────────── ops ───────────────

@app.get("/healthz", response_model=HealthResponse)
def healthz() -> HealthResponse:
    """Liveness. Must not touch the policy store or any I/O."""
    return HealthResponse(
        status="ok",
        engine_version=ENGINE_VERSION,
        policy_version=STORE.policy_version,
        policy_fitted_at=STORE.fitted_at,
        n_segments=len(STORE.segments),
        uptime_seconds=round(time.time() - _STARTED, 1),
    )


@app.get("/readyz")
def readyz() -> Dict[str, object]:
    """Readiness. Reports degraded (not failed) when running on defaults, so
    k8s keeps the pod in service — a classical (s,S) is a valid fallback."""
    fitted = bool(STORE.segments)
    return {
        "ready": True,
        "degraded": not fitted,
        "reason": None if fitted else "no fitted policy; serving classical (s,S) default",
    }


@app.get("/metrics", response_class=PlainTextResponse)
def metrics() -> str:
    out = [f"smartstock_uptime_seconds {time.time() - _STARTED:.1f}"]
    for k, v in _METRICS.items():
        out.append(f"smartstock_{k} {v}")
    out.append(f"smartstock_policy_segments {len(STORE.segments)}")
    out.append(f"smartstock_jobs_active {JOBS.active_count()}")
    return "\n".join(out) + "\n"


# ─────────────── core ───────────────

@app.post(
    "/v1/recommendations:generate",
    response_model=ReplenishmentResponse,
    dependencies=[Depends(require_key)],
)
async def generate_recommendations(req: ReplenishmentRequest) -> ReplenishmentResponse:
    """Synchronous draft-PO generation. This is the nightly ERP call."""
    n = len(req.items or [])
    if n == 0:
        raise HTTPException(status_code=422, detail="items must not be empty")
    if n > settings.max_items_per_request:
        raise HTTPException(
            status_code=413,
            detail=f"{n} items exceeds limit {settings.max_items_per_request}; page the request",
        )
    t0 = time.perf_counter()
    resp = await run_in_threadpool(
        generate, req, STORE, settings.default_lead_days, settings.default_lead_cv
    )
    dt = time.perf_counter() - t0
    _METRICS["requests_total"] += 1
    _METRICS["lines_generated"] += resp.stats.get("lines_recommended", 0)
    _METRICS["generate_seconds_total"] += dt
    logger.info(
        "run=%s items=%d lines=%d drafts=%d %.0fms",
        req.run_id, n, resp.stats.get("lines_recommended", 0),
        len(resp.draft_purchase_orders), dt * 1000,
    )
    return resp


@app.get("/v1/policy", dependencies=[Depends(require_key)])
def get_policy() -> Dict[str, object]:
    from ..core.policy import PARAM_BOUNDS, PARAM_NAMES
    return {
        "policy_version": STORE.policy_version,
        "fitted_at": STORE.fitted_at,
        "segments": STORE.segments,
        "parameters": STORE.describe(),
        "param_names": list(PARAM_NAMES),
        "param_bounds": PARAM_BOUNDS.tolist(),
    }


@app.put("/v1/policy", dependencies=[Depends(require_key)])
def put_policy(body: Dict[str, object]) -> Dict[str, object]:
    """Hot-swap fitted parameters. Atomic: validated fully before assignment."""
    params = body.get("params")
    if not isinstance(params, dict) or not params:
        raise HTTPException(status_code=422, detail="body.params must be a non-empty object")
    try:
        STORE.load(params, body.get("version"))  # type: ignore[arg-type]
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if settings.policy_path:
        try:
            import json, pathlib, tempfile
            p = pathlib.Path(settings.policy_path)
            p.parent.mkdir(parents=True, exist_ok=True)
            # Write-then-rename so a crash mid-write cannot leave a truncated
            # policy file that poisons the next startup.
            with tempfile.NamedTemporaryFile("w", dir=p.parent, delete=False) as fh:
                json.dump({"version": STORE.policy_version, "params": params}, fh)
                tmp = fh.name
            os.replace(tmp, p)
        except Exception:
            logger.exception("policy persisted in memory but not to disk")
    return {"policy_version": STORE.policy_version, "segments": STORE.segments}


@app.post("/v1/policy:optimize", response_model=JobHandle, dependencies=[Depends(require_key)])
def optimize(req: OptimizeRequest, background: BackgroundTasks) -> JobHandle:
    """Kick off a CMA-ES refit. Returns immediately with a job handle."""
    if not req.items:
        raise HTTPException(status_code=422, detail="items must not be empty")
    if req.max_generations > settings.max_fit_generations:
        raise HTTPException(
            status_code=422,
            detail=f"max_generations capped at {settings.max_fit_generations}",
        )
    job_id = f"fit-{uuid.uuid4().hex[:12]}"
    JOBS.create(job_id)
    _METRICS["fit_jobs_total"] += 1
    background.add_task(run_fit_job, JOBS, job_id, req, STORE)
    return JOBS.get(job_id)


@app.get("/v1/jobs/{job_id}", response_model=JobHandle, dependencies=[Depends(require_key)])
def get_job(job_id: str) -> JobHandle:
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"unknown job {job_id}")
    return job


@app.post("/v1/simulate", dependencies=[Depends(require_key)])
async def simulate(body: Dict[str, object]) -> Dict[str, object]:
    """What-if: evaluate a candidate parameter set without adopting it."""
    from ..training.jobs import simulate_candidate
    try:
        return await run_in_threadpool(simulate_candidate, body, STORE)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
