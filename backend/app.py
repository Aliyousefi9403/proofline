"""HTTP API. Serves the checker, the baselines, the audit log and the built UI."""
from __future__ import annotations

import json
import threading
import time
from collections import defaultdict, deque
from pathlib import Path

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import audit, config, guard, llm, naive
from .edgar import METRICS, PUBLIC_METRICS, default_store
from .pipeline import ENGINE_VERSION, Defences, verify_report

config.load_dotenv()

RATE_WINDOW_SECONDS = 60.0
RATE_LIMIT_DEFAULT = 240
RATE_LIMIT_LLM = 40
LLM_PATHS = ("/verify", "/naive", "/ask", "/guard")
FRONTEND_DIST = config.ROOT / "frontend" / "dist"
RESULT_FILES = {
    "redteam": config.ROOT / "redteam" / "results.json",
    "eval": config.ROOT / "eval" / "results.json",
    "leaderboard": config.ROOT / "eval" / "leaderboard.json",
}

app = FastAPI(title="ProofLine", version=ENGINE_VERSION, docs_url="/api/docs", openapi_url="/api/openapi.json")
router = APIRouter()
_store = default_store()
_log = audit.AuditLog()


# ---- rate limiting -----------------------------------------------------------

class RateLimiter:
    """Sliding-window limiter per client address. In-memory: one process, one demo."""

    def __init__(self) -> None:
        self._hits: dict[tuple[str, str], deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, client: str, bucket: str, limit: int) -> bool:
        now = time.monotonic()
        with self._lock:
            hits = self._hits[(client, bucket)]
            while hits and now - hits[0] > RATE_WINDOW_SECONDS:
                hits.popleft()
            if len(hits) >= limit:
                return False
            hits.append(now)
            return True


_limiter = RateLimiter()


@app.middleware("http")
async def rate_limit(request: Request, call_next):
    path = request.url.path.removeprefix("/api")
    if request.url.path.startswith("/assets"):
        return await call_next(request)
    client = request.client.host if request.client else "unknown"
    is_llm = request.method == "POST" and path.startswith(LLM_PATHS)
    bucket, limit = ("llm", RATE_LIMIT_LLM) if is_llm else ("default", RATE_LIMIT_DEFAULT)
    if not _limiter.allow(client, bucket, limit):
        return JSONResponse({"detail": "Too many requests. Wait a moment and try again."}, status_code=429)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


# ---- request models ----------------------------------------------------------

class DefenceOptions(BaseModel):
    sanitize: bool = True
    bind: bool = True
    coverage: bool = True
    anti_evasion: bool = True


class VerifyRequest(BaseModel):
    text: str = Field(min_length=1, max_length=config.MAX_REPORT_CHARS)
    coverage: bool = True
    defences: DefenceOptions | None = None
    extractor: str = Field(default="auto", pattern="^(auto|llm|rules)$")


class TextRequest(BaseModel):
    text: str = Field(min_length=1, max_length=config.MAX_REPORT_CHARS)


class GuardRequest(BaseModel):
    text: str = Field(min_length=1, max_length=config.MAX_REPORT_CHARS)
    withhold_unverified: bool = False
    extractor: str = Field(default="auto", pattern="^(auto|llm|rules)$")


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=2000)
    withhold_unverified: bool = False


class ReplayRequest(BaseModel):
    certificate: dict
    text: str | None = Field(default=None, max_length=config.MAX_REPORT_CHARS)


class AuditVerifyRequest(BaseModel):
    simulate_tamper: bool = False


def _llm_call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except llm.LLMUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except llm.LLMError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


def _defences(request: VerifyRequest) -> Defences:
    if request.defences is not None:
        return Defences(**request.defences.model_dump())
    return Defences(coverage=request.coverage)


# ---- endpoints ---------------------------------------------------------------

@router.get("/health")
def health() -> dict:
    return {"ok": True, "engine": ENGINE_VERSION, "companies": _store.tickers(), "llm": llm.provider()}


@router.get("/meta")
def meta() -> dict:
    companies = []
    for ticker in _store.tickers():
        years = _store.fiscal_years(ticker)
        companies.append({
            "ticker": ticker, "name": _store.company_name(ticker),
            "first_year": years[0] if years else None, "last_year": years[-1] if years else None,
        })
    return {
        "engine": ENGINE_VERSION,
        "companies": companies,
        "metrics": [{"key": key, "label": METRICS[key].label} for key in PUBLIC_METRICS],
        "llm": {"provider": llm.provider(), "model": llm.default_model()},
        "data_snapshot": _store.snapshot_id(),
        "results": {name: path.exists() for name, path in RESULT_FILES.items()},
    }


@router.post("/verify")
def verify(request: VerifyRequest) -> dict:
    return _llm_call(verify_report, request.text, _store, extractor=request.extractor,
                     defences=_defences(request), log=_log)


@router.post("/naive")
def naive_endpoint(request: TextRequest) -> dict:
    return _llm_call(naive.naive_check, request.text)


@router.post("/guard")
def guard_endpoint(request: GuardRequest) -> dict:
    result = _llm_call(verify_report, request.text, _store, extractor=request.extractor, log=_log)
    return {"verification": result, **guard.release(result, withhold_unverified=request.withhold_unverified)}


@router.post("/ask")
def ask(request: AskRequest) -> dict:
    """The same agent with and without ProofLine in front of it."""
    answer = _llm_call(guard.agent_answer, request.question)
    result = _llm_call(verify_report, answer["answer"], _store, log=_log)
    return {
        "question": request.question,
        "agent": answer,
        "verification": result,
        **guard.release(result, withhold_unverified=request.withhold_unverified),
    }


@router.post("/replay")
def replay(request: ReplayRequest) -> dict:
    return audit.replay_certificate(request.certificate, _store, _log, request.text)


@router.get("/audit")
def audit_tail(limit: int = 60) -> dict:
    limit = max(1, min(limit, 500))
    return {"entries": _log.tail(limit), "total": len(_log), "head": _log.head}


@router.post("/audit/verify")
def audit_verify(request: AuditVerifyRequest | None = None) -> dict:
    if request is not None and request.simulate_tamper:
        return _log.simulate_tamper()
    return _log.verify_chain()


@router.get("/samples")
def samples() -> dict:
    index = config.SAMPLES_DIR / "index.json"
    if not index.exists():
        return {"samples": []}
    out = []
    for item in json.loads(index.read_text(encoding="utf-8")):
        path = (config.SAMPLES_DIR / item["file"]).resolve()
        if config.SAMPLES_DIR.resolve() not in path.parents or not path.exists():
            continue
        out.append({**item, "text": path.read_text(encoding="utf-8")})
    return {"samples": out}


@router.get("/facts/{ticker}")
def facts(ticker: str) -> dict:
    ticker = ticker.upper()
    if not _store.has(ticker):
        raise HTTPException(status_code=404, detail=f"No cached data for {ticker}.")
    years = _store.fiscal_years(ticker)[-8:]
    rows = []
    for key in PUBLIC_METRICS:
        values = {}
        for year in years:
            fact = _store.get_fact(ticker, key, year)
            if fact is not None:
                values[str(year)] = {"value": fact.value, "url": fact.url, "accn": fact.accn,
                                     "filed": fact.filed, "derived": fact.derived}
        rows.append({"metric": key, "label": METRICS[key].label, "unit": METRICS[key].unit, "values": values})
    return {"ticker": ticker, "name": _store.company_name(ticker), "years": years, "rows": rows}


@router.get("/results/{name}")
def results(name: str) -> dict:
    path = RESULT_FILES.get(name)
    if path is None:
        raise HTTPException(status_code=404, detail="Unknown result set.")
    if not path.exists():
        return {"available": False}
    return {"available": True, **json.loads(path.read_text(encoding="utf-8"))}


app.include_router(router)
app.include_router(router, prefix="/api")

if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        candidate = (FRONTEND_DIST / path).resolve()
        if path and FRONTEND_DIST.resolve() in candidate.parents and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(FRONTEND_DIST / "index.html")
