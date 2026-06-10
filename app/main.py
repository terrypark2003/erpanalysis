"""FastAPI 서버: 분석 API + 대시보드 정적 파일 서빙."""
from __future__ import annotations

import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import providers
from .analysis import AnalysisEngine
from .config import settings
from .ecount_client import EcountApiError, test_connection

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(title="ECOUNT 재고 분석 대시보드", version="1.0.0")

_lock = threading.Lock()
_state: dict = {"result": None, "error": None}


def _build(force_refresh: bool = False) -> dict:
    dataset = providers.fetch_dataset(settings, force_refresh=force_refresh)
    result = AnalysisEngine(dataset, settings).run()
    result["meta"] = {
        "source": dataset.get("source", "demo"),
        "fetched_at": dataset.get("fetched_at"),
        "demo_mode": settings.demo_mode,
        "tx_count": len(dataset.get("transactions", [])),
    }
    return result


@app.get("/api/dashboard")
def api_dashboard():
    with _lock:
        if _state["result"] is None:
            try:
                _state["result"] = _build()
                _state["error"] = None
            except EcountApiError as e:
                raise HTTPException(status_code=502, detail=str(e))
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"분석 실패: {e}")
        return JSONResponse(_state["result"])


@app.post("/api/refresh")
def api_refresh():
    with _lock:
        try:
            _state["result"] = _build(force_refresh=True)
            _state["error"] = None
            return {"ok": True, "fetched_at": _state["result"]["meta"]["fetched_at"],
                    "source": _state["result"]["meta"]["source"]}
        except EcountApiError as e:
            _state["error"] = str(e)
            raise HTTPException(status_code=502, detail=str(e))
        except Exception as e:
            _state["error"] = str(e)
            raise HTTPException(status_code=500, detail=f"데이터 수집 실패: {e}")


@app.get("/api/status")
def api_status():
    return {
        "demo_mode": settings.demo_mode,
        "has_credentials": settings.has_credentials,
        "use_test_server": settings.use_test_server,
        "analysis_months": settings.analysis_months,
        "lead_time_days": settings.lead_time_days,
        "service_level": settings.service_level,
        "last_error": _state["error"],
    }


@app.get("/api/test-connection")
def api_test_connection():
    if not settings.has_credentials:
        return {"ok": False, "steps": [{
            "step": "0. 인증정보 확인", "ok": False,
            "detail": ".env에 ECOUNT_COM_CODE / ECOUNT_USER_ID / ECOUNT_API_CERT_KEY를 설정하세요.",
        }]}
    return test_connection(settings)


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
