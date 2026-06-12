"""ECOUNT Open API 클라이언트.

인증 흐름:
  1) Zone 조회  : POST https://oapi.ecount.com/OAPI/V2/Zone        {"COM_CODE": ...}
  2) 로그인     : POST https://oapi{ZONE}.ecount.com/OAPI/V2/OAPILogin
                  {"COM_CODE","USER_ID","API_CERT_KEY","LAN_TYPE":"ko-KR","ZONE"}
                  → Data.Datas.SESSION_ID
  3) 조회 호출  : POST https://oapi{ZONE}.ecount.com/OAPI/V2/...?SESSION_ID=...

테스트 인증키 사용 시 oapi → sboapi 도메인을 쓴다(ECOUNT_TEST_SERVER=true).

주의: 조회 API의 경로/파라미터 명칭은 ECOUNT가 계정·버전에 따라 다를 수 있다.
ERP 로그인 후 [Self-Customizing > 정보관리 > API 인증키 발급]과 OpenAPI 가이드
(https://sboapi.ecount.com/ECERP/OAPI/OAPIView)에서 확인 후 ENDPOINTS만 바꾸면 된다.
"""
from __future__ import annotations

import time
from datetime import date, timedelta
from typing import Any

import httpx

# 조회 API 경로 — ECOUNT OpenAPI 가이드와 다르면 여기만 수정하면 된다.
ENDPOINTS = {
    "zone": "/OAPI/V2/Zone",
    "login": "/OAPI/V2/OAPILogin",
    # 재고현황(기준일자 시점 품목별 재고)
    "inventory_balance": "/OAPI/V2/InventoryBalance/GetListInventoryBalanceStatus",
    # 품목 기본정보 목록
    "products": "/OAPI/V2/InventoryBasic/GetBasicProductsList",
    # 판매현황(기간 내 판매 내역) — 계정에 따라 경로가 다를 수 있음
    "sales": "/OAPI/V2/Sale/GetListSale",
    # 구매현황(입고) — 회전율 보정용(없어도 분석은 동작)
    "purchases": "/OAPI/V2/Purchases/GetListPurchases",
}

# ECOUNT 응답 필드명이 계정 설정에 따라 다른 경우를 흡수하기 위한 후보 키 목록
FIELD_CANDIDATES = {
    "prod_cd": ["PROD_CD", "PROD_CODE", "ITEM_CD"],
    "prod_des": ["PROD_DES", "PROD_NAME", "ITEM_NM", "PROD_DES2"],
    "size_des": ["SIZE_DES", "SPEC", "STANDARD"],
    "unit": ["UNIT", "UNIT_DES"],
    "class_des": ["CLASS_DES", "CLASS_CD", "PROD_TYPE", "CLASS_DES2"],
    "in_price": ["IN_PRICE", "PURCHASE_PRICE", "PRICE_IN", "STND_PRICE"],
    "out_price": ["OUT_PRICE", "SALE_PRICE", "PRICE_OUT", "OUT_PRICE1"],
    "bal_qty": ["BAL_QTY", "QTY", "STOCK_QTY", "REMAIN_QTY"],
    "io_date": ["IO_DATE", "SALE_DATE", "DATE", "IO_DATE_F"],
    "qty": ["QTY", "SALE_QTY", "OUT_QTY", "IO_QTY"],
    "amount": ["SUPPLY_AMT", "AMT", "SALE_AMT", "TOTAL_AMT", "SUPPLY_AMT_F"],
}


class EcountApiError(RuntimeError):
    def __init__(self, step: str, message: str, payload: Any = None):
        super().__init__(f"[{step}] {message}")
        self.step = step
        self.payload = payload


def pick(row: dict, field: str, default=None):
    """후보 키 목록에서 첫 번째로 존재하는 값을 반환."""
    for key in FIELD_CANDIDATES.get(field, []):
        if key in row and row[key] not in (None, ""):
            return row[key]
    return default


def _to_float(v, default=0.0) -> float:
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return default


def _normalize_date(v: str) -> str | None:
    """'20240105' / '2024-01-05' / '2024/01/05' → '2024-01-05'"""
    if not v:
        return None
    s = str(v).strip().replace("/", "-")
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    if len(s) >= 10:
        return s[:10]
    return None


class EcountClient:
    """세션 관리 + 공통 호출. ECOUNT 호출 제한을 고려해 호출 간 지연을 둔다."""

    def __init__(self, com_code: str, user_id: str, api_cert_key: str,
                 zone: str = "", use_test_server: bool = False,
                 call_interval: float = 1.1, timeout: float = 30.0,
                 proxy: str = ""):
        self.com_code = com_code
        self.user_id = user_id
        self.api_cert_key = api_cert_key
        self.zone = zone
        self.subdomain = "sboapi" if use_test_server else "oapi"
        self.call_interval = call_interval
        self.session_id: str | None = None
        self._last_call = 0.0
        # 고정 IP 프록시(ECOUNT IP 허용목록 대응) — 설정 시 모든 호출이 경유한다
        client_kwargs: dict = {"timeout": timeout,
                               "headers": {"Content-Type": "application/json"}}
        if proxy:
            client_kwargs["proxy"] = proxy
        self._http = httpx.Client(**client_kwargs)

    # ------------------------------------------------------------------
    def _throttle(self):
        wait = self.call_interval - (time.monotonic() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.monotonic()

    def _post(self, url: str, body: dict, step: str) -> dict:
        last_err: Exception | None = None
        for attempt in range(3):
            self._throttle()
            try:
                resp = self._http.post(url, json=body)
            except httpx.HTTPError as e:
                last_err = e
                time.sleep(2 ** attempt)
                continue
            if resp.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                time.sleep(2 ** attempt)
                continue
            if resp.status_code != 200:
                raise EcountApiError(step, f"HTTP {resp.status_code}: {resp.text[:300]}")
            try:
                data = resp.json()
            except ValueError:
                raise EcountApiError(step, f"JSON 응답이 아님: {resp.text[:300]}")
            status = str(data.get("Status", ""))
            if status and status != "200":
                err = data.get("Error") or data.get("Errors") or data
                raise EcountApiError(step, f"ECOUNT 오류(Status={status}): {str(err)[:400]}", data)
            return data
        raise EcountApiError(step, f"네트워크 오류로 호출 실패: {last_err}")

    # ------------------------------------------------------------------
    def resolve_zone(self) -> str:
        if self.zone:
            return self.zone
        url = f"https://{self.subdomain}.ecount.com{ENDPOINTS['zone']}"
        data = self._post(url, {"COM_CODE": self.com_code}, "Zone조회")
        zone = (data.get("Data") or {}).get("ZONE", "")
        if not zone:
            raise EcountApiError("Zone조회", f"ZONE 값을 찾지 못함: {str(data)[:300]}", data)
        self.zone = zone
        return zone

    @property
    def base_url(self) -> str:
        return f"https://{self.subdomain}{self.zone}.ecount.com"

    def login(self) -> str:
        self.resolve_zone()
        url = f"{self.base_url}{ENDPOINTS['login']}"
        body = {
            "COM_CODE": self.com_code,
            "USER_ID": self.user_id,
            "API_CERT_KEY": self.api_cert_key,
            "LAN_TYPE": "ko-KR",
            "ZONE": self.zone,
        }
        data = self._post(url, body, "로그인")
        datas = (data.get("Data") or {}).get("Datas") or {}
        session_id = datas.get("SESSION_ID", "")
        if not session_id:
            raise EcountApiError("로그인", f"SESSION_ID를 찾지 못함: {str(data)[:300]}", data)
        self.session_id = session_id
        return session_id

    def call(self, endpoint_key: str, body: dict) -> list[dict]:
        """조회 API 호출 후 결과 행 목록을 반환."""
        if not self.session_id:
            self.login()
        url = f"{self.base_url}{ENDPOINTS[endpoint_key]}?SESSION_ID={self.session_id}"
        data = self._post(url, body, endpoint_key)
        return self._extract_rows(data)

    @staticmethod
    def _extract_rows(data: dict) -> list[dict]:
        """ECOUNT 응답에서 행 배열을 찾아낸다(Data.Result / Data.Datas / Data 등)."""
        node = data.get("Data", data)
        if isinstance(node, list):
            return node
        if isinstance(node, dict):
            for key in ("Result", "Datas", "Rows", "List", "Items"):
                v = node.get(key)
                if isinstance(v, list):
                    return v
                if isinstance(v, dict):
                    for v2 in v.values():
                        if isinstance(v2, list):
                            return v2
        return []

    def close(self):
        self._http.close()


# ----------------------------------------------------------------------
def fetch_dataset(settings) -> dict[str, Any]:
    """ECOUNT에서 품목/재고/판매(+구매) 데이터를 수집해 정규화된 데이터셋으로 반환."""
    client = EcountClient(
        com_code=settings.com_code,
        user_id=settings.user_id,
        api_cert_key=settings.api_cert_key,
        zone=settings.zone,
        use_test_server=settings.use_test_server,
        proxy=settings.ecount_proxy,
    )
    try:
        client.login()
        as_of = date.today()
        base_date = as_of.strftime("%Y%m%d")

        # 1) 품목 기본정보
        items = []
        try:
            rows = client.call("products", {"PROD_CD": "", "BASE_DATE": base_date})
            for r in rows:
                code = str(pick(r, "prod_cd", "")).strip()
                if not code:
                    continue
                items.append({
                    "code": code,
                    "name": str(pick(r, "prod_des", code)),
                    "spec": str(pick(r, "size_des", "") or ""),
                    "unit": str(pick(r, "unit", "") or ""),
                    "category": str(pick(r, "class_des", "") or "(미분류)"),
                    "in_price": _to_float(pick(r, "in_price")),
                    "out_price": _to_float(pick(r, "out_price")),
                })
        except EcountApiError:
            # 품목 API가 막혀 있어도 재고/판매 데이터만으로 분석 가능
            items = []

        # 2) 재고 스냅샷 — 최근 ~84일을 약 3주 간격으로 여러 시점 조회한다.
        #    ECOUNT는 과거 기준일 재고조회를 약 3개월까지만 허용하므로 그 범위에서만 본다.
        price_in = {it["code"]: it["in_price"] for it in items}
        price_out = {it["code"]: it["out_price"] for it in items}
        snapshots: list[tuple[date, dict[str, float]]] = []
        for days_ago in (84, 63, 42, 21, 0):
            d = as_of - timedelta(days=days_ago)
            try:
                rows = client.call("inventory_balance", {"BASE_DATE": d.strftime("%Y%m%d")})
            except EcountApiError:
                continue  # 너무 과거라 거부(412)되면 해당 시점은 건너뛴다
            snap: dict[str, float] = {}
            for r in rows:
                code = str(pick(r, "prod_cd", "")).strip()
                if not code:
                    continue
                snap[code] = _to_float(pick(r, "bal_qty"))
                if code not in price_out:  # 품목 API에 없던 코드 보강
                    items.append({
                        "code": code, "name": str(pick(r, "prod_des", code)),
                        "spec": "", "unit": "", "category": "(미분류)",
                        "in_price": 0, "out_price": 0,
                    })
                    price_in[code] = 0.0
                    price_out[code] = 0.0
            snapshots.append((d, snap))

        if not snapshots:
            raise EcountApiError("재고조회", "재고현황을 한 건도 받지 못했습니다.")
        snapshots.sort(key=lambda x: x[0])  # 과거 → 현재 순

        # 현재고 = 가장 최근 스냅샷
        stock = [{"code": c, "qty": q} for c, q in snapshots[-1][1].items()]

        # 3) 재고 변동을 합성 거래로 환산: 감소=출고(판매), 증가=입고(구매).
        #    판매 조회 API가 없는 ECOUNT 구조라, 시점 간 재고 차이로 출고량을 추정한다.
        transactions: list[dict] = []
        for (_d_prev, sp), (d_cur, sc) in zip(snapshots, snapshots[1:]):
            iso = d_cur.isoformat()
            for code in set(sp) | set(sc):
                delta = sp.get(code, 0.0) - sc.get(code, 0.0)
                if delta > 0:  # 재고가 줄었다 = 빠져나감(판매)
                    transactions.append({
                        "date": iso, "code": code, "io": "OUT",
                        "qty": delta, "amount": delta * price_out.get(code, 0.0),
                    })
                elif delta < 0:  # 재고가 늘었다 = 입고(구매)
                    transactions.append({
                        "date": iso, "code": code, "io": "IN",
                        "qty": -delta, "amount": -delta * price_in.get(code, 0.0),
                    })

        return {
            "as_of": as_of.isoformat(),
            "source": "ecount",
            "items": items,
            "stock": stock,
            "transactions": transactions,
        }
    finally:
        client.close()


def test_connection(settings) -> dict[str, Any]:
    """단계별 연결 진단: Zone → 로그인 → 재고현황 1회 조회."""
    result = {"steps": [], "ok": False}
    client = EcountClient(
        com_code=settings.com_code,
        user_id=settings.user_id,
        api_cert_key=settings.api_cert_key,
        zone=settings.zone,
        use_test_server=settings.use_test_server,
        proxy=settings.ecount_proxy,
    )
    try:
        try:
            zone = client.resolve_zone()
            result["steps"].append({"step": "1. Zone 조회", "ok": True, "detail": f"ZONE={zone}"})
        except Exception as e:
            result["steps"].append({"step": "1. Zone 조회", "ok": False, "detail": str(e)})
            return result
        try:
            client.login()
            result["steps"].append({"step": "2. 로그인(SESSION_ID 발급)", "ok": True,
                                    "detail": "세션 발급 성공"})
        except Exception as e:
            result["steps"].append({"step": "2. 로그인(SESSION_ID 발급)", "ok": False,
                                    "detail": str(e)})
            return result
        try:
            rows = client.call("inventory_balance",
                               {"BASE_DATE": date.today().strftime("%Y%m%d")})
            result["steps"].append({"step": "3. 재고현황 조회", "ok": True,
                                    "detail": f"{len(rows)}개 행 수신"})
            result["ok"] = True
        except Exception as e:
            result["steps"].append({"step": "3. 재고현황 조회", "ok": False, "detail": str(e)})
        return result
    finally:
        client.close()
