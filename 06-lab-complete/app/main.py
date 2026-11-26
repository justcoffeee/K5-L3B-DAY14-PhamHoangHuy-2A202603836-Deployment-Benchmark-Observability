"""
Production AI Agent — Part 6 Final Project

Kiểm tra:
    python check_production_ready.py
"""
import json
import logging
import sys
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.auth import verify_api_key
from app.config import settings
from app.cost_guard import check_budget, estimate_tokens, record_cost
from app.rate_limiter import check_rate_limit
from app.redis_client import redis_healthy
from utils.mock_llm import ask as llm_ask


# ── Structured logging: mỗi dòng log là 1 JSON object ──────────────────────
class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "lvl": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        entry.update(getattr(record, "extra_fields", {}))
        if record.exc_info:
            entry["exc_type"] = record.exc_info[0].__name__
        return json.dumps(entry, ensure_ascii=False)


def _setup_logging() -> logging.Logger:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    log = logging.getLogger("agent")
    log.handlers[:] = [handler]
    log.setLevel(settings.log_level)
    log.propagate = False
    return log


logger = _setup_logging()


def log_event(event: str, level: int = logging.INFO, **fields) -> None:
    logger.log(level, event, extra={"extra_fields": fields})


# ── Lifespan: startup / graceful shutdown ──────────────────────────────────
STATE = {"shutting_down": False, "in_flight": 0}


@asynccontextmanager
async def lifespan(_: FastAPI):
    log_event("startup", environment=settings.environment, port=settings.port,
              redis_ok=redis_healthy())
    yield
    # Uvicorn nhận SIGTERM → ngừng nhận kết nối mới, chờ request đang chạy xong,
    # rồi mới chạy tới đây. Ở đây chỉ dọn dẹp và ghi log.
    STATE["shutting_down"] = True
    log_event("shutdown", in_flight=STATE["in_flight"])


app = FastAPI(title="Production AI Agent", lifespan=lifespan)


@app.middleware("http")
async def access_log(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
    start = time.perf_counter()
    STATE["in_flight"] += 1
    try:
        response = await call_next(request)
    finally:
        STATE["in_flight"] -= 1
    ms = round((time.perf_counter() - start) * 1000, 1)
    response.headers["X-Request-ID"] = request_id
    log_event("request", method=request.method, path=request.url.path,
              status=response.status_code, ms=ms, request_id=request_id)
    return response


# ── Xử lý lỗi: mã lỗi rõ ràng, không lộ stack trace ────────────────────────
@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, exc: RequestValidationError):
    errors = [{"field": ".".join(str(p) for p in e["loc"][1:]), "msg": e["msg"]}
              for e in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": "Invalid request", "errors": errors})


@app.exception_handler(Exception)
async def unhandled_error(_: Request, exc: Exception):
    log_event("unhandled_error", logging.ERROR, error_type=type(exc).__name__)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


# ── Endpoints ──────────────────────────────────────────────────────────────
class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    user_id: str | None = Field(default=None, max_length=128)


@app.get("/health")
def health():
    """Liveness: process còn sống là 200, không phụ thuộc Redis."""
    return {"status": "ok"}


@app.get("/ready")
def ready():
    """Readiness: chỉ nhận traffic khi Redis kết nối được và chưa shutdown."""
    if STATE["shutting_down"]:
        return JSONResponse(status_code=503, content={"status": "shutting_down"})
    if not redis_healthy():
        return JSONResponse(status_code=503, content={"status": "not_ready", "redis": "down"})
    return {"status": "ready", "redis": "ok"}


@app.post("/ask")
def ask(body: AskRequest, api_key: str = Depends(verify_api_key)):
    check_rate_limit(api_key)
    check_budget()
    answer = llm_ask(body.question)
    cost = record_cost(estimate_tokens(body.question), estimate_tokens(answer))
    log_event("ask", user_id=body.user_id, q_len=len(body.question), cost_usd=round(cost, 8))
    return {"question": body.question, "answer": answer}
