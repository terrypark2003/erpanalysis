"""FastAPI 서버: 분석 API + 대시보드 정적 파일 서빙."""
from __future__ import annotations

import base64
import secrets
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
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

# 진단용 경로는 비밀번호 없이 허용(영업 데이터 미포함)
_OPEN_PATHS = {"/api/status", "/api/test-connection", "/api/test-collect",
               "/api/probe-endpoints", "/api/test-history", "/api/test-build"}


@app.middleware("http")
async def access_guard(request: Request, call_next):
    """DASHBOARD_PASSWORD가 설정되면 진단 경로 외 전체에 Basic 인증을 요구한다."""
    pwd = settings.dashboard_password
    if pwd and request.url.path not in _OPEN_PATHS:
        ok = False
        auth = request.headers.get("authorization", "")
        if auth.startswith("Basic "):
            try:
                raw = base64.b64decode(auth[6:]).decode("utf-8")
                given = raw.split(":", 1)[1] if ":" in raw else ""
                ok = secrets.compare_digest(given, pwd)
            except Exception:
                ok = False
        if not ok:
            return Response(
                status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="dashboard", charset="UTF-8"'},
            )
    return await call_next(request)


def _require_protection():
    """실데이터 모드인데 비밀번호가 없으면 데이터 제공을 거부한다(공개 URL 노출 방지)."""
    if not settings.demo_mode and not settings.dashboard_password:
        raise HTTPException(
            status_code=403,
            detail="실데이터 모드에서는 DASHBOARD_PASSWORD 환경변수를 설정해야 합니다. "
                   "공개 URL로 회사 매출·재고가 노출되는 것을 막기 위한 안전장치입니다. "
                   "(Vercel: Settings → Environment Variables에 DASHBOARD_PASSWORD 추가 후 재배포)",
        )


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
    _require_protection()
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
    _require_protection()
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


@app.get("/api/test-collect")
def api_test_collect():
    """전체 수집 경로(품목/재고/판매/구매)를 각 1회 시험 호출해 단계별 행수·오류만
    반환한다. 실제 영업 데이터는 반환하지 않으므로 진단용으로 열어둔다."""
    if settings.demo_mode:
        return {"ok": False, "steps": [
            {"step": "0. 모드", "ok": False, "detail": "데모 모드입니다. 인증정보를 설정하세요."}]}
    from datetime import date, timedelta
    from .ecount_client import EcountClient
    today = date.today()
    base = today.strftime("%Y%m%d")
    month_ago = (today - timedelta(days=30)).strftime("%Y%m%d")
    client = EcountClient(
        com_code=settings.com_code, user_id=settings.user_id,
        api_cert_key=settings.api_cert_key, zone=settings.zone,
        use_test_server=settings.use_test_server, proxy=settings.ecount_proxy,
    )
    steps = []
    try:
        try:
            client.login()
            steps.append({"step": "로그인", "ok": True, "detail": "세션 발급"})
        except Exception as e:
            steps.append({"step": "로그인", "ok": False, "detail": str(e)[:300]})
            return {"steps": steps, "ok": False}
        probes = [
            ("products(품목)", "products", {"PROD_CD": "", "BASE_DATE": base}),
            ("inventory(재고)", "inventory_balance", {"BASE_DATE": base}),
            ("sales(판매·최근30일)", "sales", {"FROM_DATE": month_ago, "TO_DATE": base}),
            ("purchases(구매·최근30일)", "purchases", {"FROM_DATE": month_ago, "TO_DATE": base}),
        ]
        for label, key, body in probes:
            try:
                rows = client.call(key, body)
                steps.append({"step": label, "ok": True, "detail": f"{len(rows)}행 수신"})
            except Exception as e:
                steps.append({"step": label, "ok": False, "detail": str(e)[:300]})
    finally:
        client.close()
    return {"steps": steps, "ok": all(s["ok"] for s in steps)}


@app.get("/api/probe-endpoints")
def api_probe_endpoints():
    """판매/구매 조회 API의 올바른 경로를 찾기 위해 후보 경로들을 직접 호출하고
    HTTP 상태·응답 일부를 반환한다(진단용). 200이면 정답 경로."""
    if settings.demo_mode:
        return {"ok": False, "detail": "데모 모드"}
    import time
    from datetime import date, timedelta
    from .ecount_client import EcountClient
    today = date.today()
    base = today.strftime("%Y%m%d")
    month_ago = (today - timedelta(days=30)).strftime("%Y%m%d")
    candidates = [
        "/OAPI/V2/Sale/GetListSaleStatus",
        "/OAPI/V2/Sale/GetSaleList",
        "/OAPI/V2/Sale/GetListSales",
        "/OAPI/V2/Sales/GetListSalesStatus",
        "/OAPI/V2/SaleBasic/GetListSale",
        "/OAPI/V2/Sale/GetBasicSaleList",
    ]
    client = EcountClient(
        com_code=settings.com_code, user_id=settings.user_id,
        api_cert_key=settings.api_cert_key, zone=settings.zone,
        use_test_server=settings.use_test_server, proxy=settings.ecount_proxy,
    )
    out = []
    try:
        client.login()
        body = {"FROM_DATE": month_ago, "TO_DATE": base}
        for path in candidates:
            url = f"{client.base_url}{path}?SESSION_ID={client.session_id}"
            try:
                r = client._http.post(url, json=body)
                out.append({"path": path, "http": r.status_code, "snippet": r.text[:120]})
            except Exception as e:
                out.append({"path": path, "error": str(e)[:120]})
            time.sleep(0.5)
    finally:
        client.close()
    return {"results": out}


@app.get("/api/test-history")
def api_test_history():
    """재고현황을 과거 기준일(BASE_DATE)로 조회해 시점별 총재고수량을 비교한다.
    시점마다 총량이 다르면 '기준일 조회'가 지원되는 것 → 재고 변동으로 출고(판매)를
    역산할 수 있다. 진단용(실데이터 미반환, 합계만)."""
    if settings.demo_mode:
        return {"ok": False, "detail": "데모 모드"}
    from datetime import date, timedelta
    from .ecount_client import EcountClient, pick, _to_float
    client = EcountClient(
        com_code=settings.com_code, user_id=settings.user_id,
        api_cert_key=settings.api_cert_key, zone=settings.zone,
        use_test_server=settings.use_test_server, proxy=settings.ecount_proxy,
    )
    today = date.today()
    targets = [("현재", today),
               ("90일전", today - timedelta(days=90)),
               ("180일전", today - timedelta(days=180))]
    out = []
    try:
        client.login()
        for label, d in targets:
            try:
                rows = client.call("inventory_balance", {"BASE_DATE": d.strftime("%Y%m%d")})
                total = round(sum(_to_float(pick(r, "bal_qty")) for r in rows), 1)
                out.append({"기준일": d.isoformat(), "라벨": label,
                            "품목수": len(rows), "총재고수량": total})
            except Exception as e:
                out.append({"기준일": d.isoformat(), "error": str(e)[:200]})
    finally:
        client.close()
    return {"results": out,
            "해석": "총재고수량이 기준일마다 다르면 과거조회 지원 → 출고 역산 가능"}


@app.get("/api/test-build")
def api_test_build():
    """수집 메커니즘 경량 점검: 품목 가격 유입 여부 + 재고 2시점 변동 환산만
    집계로 반환(개별 데이터·금액 총액 미반환)."""
    if settings.demo_mode:
        return {"ok": False, "detail": "데모 모드"}
    from datetime import date, timedelta
    from .ecount_client import EcountClient, pick, _to_float
    client = EcountClient(
        com_code=settings.com_code, user_id=settings.user_id,
        api_cert_key=settings.api_cert_key, zone=settings.zone,
        use_test_server=settings.use_test_server, proxy=settings.ecount_proxy,
    )
    try:
        client.login()
        base = date.today()
        prod_rows = client.call("products", {"PROD_CD": "", "BASE_DATE": base.strftime("%Y%m%d")})
        with_out = with_in = 0
        for r in prod_rows:
            if not str(pick(r, "prod_cd", "")).strip():
                continue
            if _to_float(pick(r, "out_price")) > 0:
                with_out += 1
            if _to_float(pick(r, "in_price")) > 0:
                with_in += 1
        snaps: dict = {}
        for days in (0, 84):
            d = base - timedelta(days=days)
            try:
                rows = client.call("inventory_balance", {"BASE_DATE": d.strftime("%Y%m%d")})
                snaps[days] = {str(pick(x, "prod_cd", "")).strip(): _to_float(pick(x, "bal_qty"))
                               for x in rows if str(pick(x, "prod_cd", "")).strip()}
            except Exception as e:
                snaps[days] = {"_error": str(e)[:80]}
        out_cnt = 0
        out_units = 0.0
        cur, old = snaps.get(0, {}), snaps.get(84, {})
        if "_error" not in cur and "_error" not in old:
            for c in set(old) | set(cur):
                drop = old.get(c, 0.0) - cur.get(c, 0.0)
                if drop > 0:
                    out_cnt += 1
                    out_units += drop
        return {
            "ok": True,
            "품목수": sum(1 for r in prod_rows if str(pick(r, "prod_cd", "")).strip()),
            "판매가>0 품목": with_out,
            "구매가>0 품목": with_in,
            "재고_현재_품목수": len(cur) if "_error" not in cur else cur,
            "재고_84일전_품목수": len(old) if "_error" not in old else old,
            "감소(출고)품목수": out_cnt,
            "84일간_총감소수량": round(out_units, 1),
        }
    finally:
        client.close()


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
