"""
Resilient HTTP client for the SmartStock service.

The ERP must never be taken down by the AI service. Every call is bounded by a
timeout, retried on transient failure with jittered backoff, and gated by a
circuit breaker. When SmartStock is unavailable the replenishment run is marked
FAILED and procurement falls back to manual POs — the ERP keeps working.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger(__name__)

RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}


class SmartStockError(RuntimeError):
    def __init__(self, message: str, status: Optional[int] = None, body: Optional[str] = None):
        super().__init__(message)
        self.status = status
        self.body = body


class CircuitOpen(SmartStockError):
    pass


@dataclass
class CircuitBreaker:
    """Trips after `threshold` consecutive failures, half-opens after `reset_after`."""

    threshold: int = 5
    reset_after: float = 60.0
    _failures: int = 0
    _opened_at: float = 0.0

    @property
    def is_open(self) -> bool:
        if self._failures < self.threshold:
            return False
        if time.time() - self._opened_at > self.reset_after:
            self._failures = self.threshold - 1   # half-open: allow one probe
            return False
        return True

    def record_success(self) -> None:
        self._failures = 0

    def record_failure(self) -> None:
        self._failures += 1
        if self._failures >= self.threshold:
            self._opened_at = time.time()
            logger.error("SmartStock circuit breaker OPEN after %d failures", self._failures)


class SmartStockClient:
    def __init__(
        self,
        base_url: str,
        api_key: str = "",
        timeout: float = 120.0,
        max_retries: int = 3,
        breaker: Optional[CircuitBreaker] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.breaker = breaker or CircuitBreaker()
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["X-API-Key"] = api_key
        # Generous connect timeout, long read: a 50k-line generate is real work.
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers=headers,
            timeout=httpx.Timeout(timeout, connect=5.0),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request(self, method: str, path: str, **kwargs: Any) -> Dict[str, Any]:
        if self.breaker.is_open:
            raise CircuitOpen("SmartStock circuit breaker is open; skipping call")

        last: Optional[Exception] = None
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = await self._client.request(method, path, **kwargs)
                if resp.status_code in RETRYABLE_STATUS:
                    raise SmartStockError(
                        f"retryable status {resp.status_code}",
                        status=resp.status_code,
                        body=resp.text[:1000],
                    )
                if resp.status_code >= 400:
                    # 4xx other than the retryable set is our bug. Fail fast;
                    # retrying a malformed payload just wastes the window.
                    self.breaker.record_success()
                    raise SmartStockError(
                        f"SmartStock returned {resp.status_code}",
                        status=resp.status_code,
                        body=resp.text[:1000],
                    )
                self.breaker.record_success()
                return resp.json()
            except (httpx.TimeoutException, httpx.TransportError, SmartStockError) as exc:
                if isinstance(exc, SmartStockError) and exc.status not in RETRYABLE_STATUS:
                    raise
                last = exc
                self.breaker.record_failure()
                if attempt == self.max_retries:
                    break
                backoff = min(30.0, 2 ** attempt) * (0.5 + random.random())
                logger.warning(
                    "SmartStock %s %s attempt %d/%d failed (%s); retrying in %.1fs",
                    method, path, attempt, self.max_retries, exc, backoff,
                )
                await asyncio.sleep(backoff)

        raise SmartStockError(f"SmartStock unreachable after {self.max_retries} attempts: {last}")

    async def health(self) -> Dict[str, Any]:
        return await self._request("GET", "/healthz")

    async def generate(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return await self._request("POST", "/v1/recommendations:generate", json=payload)

    async def optimize(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return await self._request("POST", "/v1/policy:optimize", json=payload)

    async def job(self, job_id: str) -> Dict[str, Any]:
        return await self._request("GET", f"/v1/jobs/{job_id}")

    async def get_policy(self) -> Dict[str, Any]:
        return await self._request("GET", "/v1/policy")
