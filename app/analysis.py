"""재고 분석 엔진.

정규화된 데이터셋(품목/현재고/입출고 내역)을 받아 품목별 경영 지표를 계산한다.

데이터셋 형식:
    {
      "as_of": "YYYY-MM-DD",            # 기준일(현재고 시점)
      "items": [{"code","name","spec","unit","category","in_price","out_price"}],
      "stock": [{"code","qty"}],
      "transactions": [{"date","code","io"("IN"|"OUT"),"qty","amount"}],
    }

계산 지표(품목별):
  - 재고회전율(연환산, 수량/금액 기준), 재고일수(DOI)
  - 소진예상일수(현재고 ÷ 최근 일평균 출고)
  - ABC 등급(기간 출고금액 누적 점유율)
  - 체화 등급(마지막 출고 후 경과일: 정상/주의/체화/악성)
  - 판매 추세(최근 3개월 vs 직전 3개월 성장률, 월별 시계열)
  - 안전재고 = z × σ(일출고) × √리드타임, 재주문점 = 일평균출고 × 리드타임 + 안전재고
  - 교차비율 = 회전율(금액) × 마진율
  - 재고자산 금액 = 현재고 × 입고단가
"""
from __future__ import annotations

import math
import statistics
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any

# 서비스 수준 → 정규분포 z값
Z_TABLE = [
    (0.999, 3.090), (0.99, 2.326), (0.98, 2.054), (0.97, 1.881),
    (0.95, 1.645), (0.93, 1.476), (0.90, 1.282), (0.85, 1.036), (0.80, 0.842),
]

GRADE_NORMAL = "정상"
GRADE_CAUTION = "주의"
GRADE_STAGNANT = "체화"
GRADE_DEAD = "악성"

TREND_UP = "상승"
TREND_DOWN = "하락"
TREND_FLAT = "유지"
TREND_NONE = "판매없음"


def z_for_service_level(level: float) -> float:
    for cut, z in Z_TABLE:
        if level >= cut:
            return z
    return 0.842


def parse_date(s: str) -> date:
    return datetime.strptime(s[:10], "%Y-%m-%d").date()


def month_key(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def shift_month(d: date, n: int) -> date:
    """d가 속한 달에서 n개월 이동한 달의 1일."""
    total = d.year * 12 + (d.month - 1) + n
    return date(total // 12, total % 12 + 1, 1)


def month_keys_range(end: date, months: int) -> list[str]:
    """end가 속한 달을 마지막으로 months개의 월 키 목록(과거→현재)."""
    return [month_key(shift_month(end, -(months - 1 - i))) for i in range(months)]


def least_squares_slope(values: list[float]) -> float:
    """0,1,2,... 인덱스에 대한 최소제곱 기울기."""
    n = len(values)
    if n < 2:
        return 0.0
    mean_x = (n - 1) / 2
    mean_y = sum(values) / n
    denom = sum((i - mean_x) ** 2 for i in range(n))
    if denom == 0:
        return 0.0
    return sum((i - mean_x) * (v - mean_y) for i, v in enumerate(values)) / denom


class AnalysisEngine:
    def __init__(self, dataset: dict[str, Any], config: Any):
        self.cfg = config
        self.as_of = parse_date(dataset["as_of"])
        self.items = {it["code"]: it for it in dataset["items"]}
        self.stock = {s["code"]: float(s.get("qty") or 0) for s in dataset["stock"]}
        self.period_start = shift_month(self.as_of, -(config.analysis_months - 1))
        self.period_days = max((self.as_of - self.period_start).days + 1, 1)

        # 기간 내 거래만 사용. 품목 마스터에 없는 코드도 거래/재고에 있으면 품목으로 인정.
        self.tx: list[dict] = []
        for t in dataset["transactions"]:
            d = parse_date(t["date"])
            if d > self.as_of:
                continue
            t = {**t, "_d": d}
            self.tx.append(t)
            if t["code"] not in self.items:
                self.items[t["code"]] = {"code": t["code"], "name": t["code"], "spec": "",
                                         "unit": "", "category": "(미분류)",
                                         "in_price": 0, "out_price": 0}
        for code in self.stock:
            if code not in self.items:
                self.items[code] = {"code": code, "name": code, "spec": "", "unit": "",
                                    "category": "(미분류)", "in_price": 0, "out_price": 0}

    # ------------------------------------------------------------------
    def run(self) -> dict[str, Any]:
        per_item = self._collect_per_item()
        rows = [self._compute_item(code, agg) for code, agg in per_item.items()]
        self._assign_abc(rows)
        for r in rows:
            r["action"] = self._recommend(r)
        rows.sort(key=lambda r: r["out_amount"], reverse=True)
        summary = self._summarize(rows)
        return {
            "as_of": self.as_of.isoformat(),
            "period_start": self.period_start.isoformat(),
            "period_months": self.cfg.analysis_months,
            "month_keys": month_keys_range(self.as_of, self.cfg.analysis_months),
            "items": rows,
            "summary": summary,
            "params": {
                "lead_time_days": self.cfg.lead_time_days,
                "service_level": self.cfg.service_level,
                "recent_window_days": self.cfg.recent_window_days,
                "caution_days": self.cfg.caution_days,
                "stagnant_days": self.cfg.stagnant_days,
                "dead_days": self.cfg.dead_days,
            },
        }

    # ------------------------------------------------------------------
    def _collect_per_item(self) -> dict[str, dict]:
        """품목별 거래 집계(월별/일별 시계열 포함)."""
        per: dict[str, dict] = {}
        for code in self.items:
            per[code] = {
                "out_qty": 0.0, "out_amount": 0.0, "in_qty": 0.0,
                "monthly_out_qty": defaultdict(float), "monthly_out_amt": defaultdict(float),
                "monthly_in_qty": defaultdict(float),
                "daily_out_recent": defaultdict(float),  # 최근 N일 일별 출고
                "last_out": None, "first_tx": None,
            }
        recent_start = self.as_of - timedelta(days=self.cfg.recent_window_days - 1)
        for t in self.tx:
            d: date = t["_d"]
            agg = per[t["code"]]
            if agg["first_tx"] is None or d < agg["first_tx"]:
                agg["first_tx"] = d
            if d < self.period_start:
                continue
            mk = month_key(d)
            qty = float(t.get("qty") or 0)
            if t["io"] == "OUT":
                item = self.items[t["code"]]
                amount = float(t.get("amount") or 0) or qty * float(item.get("out_price") or 0)
                agg["out_qty"] += qty
                agg["out_amount"] += amount
                agg["monthly_out_qty"][mk] += qty
                agg["monthly_out_amt"][mk] += amount
                if agg["last_out"] is None or d > agg["last_out"]:
                    agg["last_out"] = d
                if d >= recent_start:
                    agg["daily_out_recent"][d.isoformat()] += qty
            else:
                agg["in_qty"] += qty
                agg["monthly_in_qty"][mk] += qty
        return per

    # ------------------------------------------------------------------
    def _month_end_balances(self, code: str, agg: dict) -> list[float]:
        """현재고에서 거꾸로 월말 재고를 복원(과거→현재 순서로 반환)."""
        keys = month_keys_range(self.as_of, self.cfg.analysis_months)
        bal = self.stock.get(code, 0.0)
        balances = {keys[-1]: bal}
        # 마지막 달부터 거꾸로: 직전 월말 = 현재 월말 - 입고 + 출고
        for i in range(len(keys) - 1, 0, -1):
            mk = keys[i]
            bal = bal - agg["monthly_in_qty"].get(mk, 0.0) + agg["monthly_out_qty"].get(mk, 0.0)
            balances[keys[i - 1]] = bal
        return [max(balances[k], 0.0) for k in keys]

    # ------------------------------------------------------------------
    def _compute_item(self, code: str, agg: dict) -> dict[str, Any]:
        item = self.items[code]
        cfg = self.cfg
        cur_qty = self.stock.get(code, 0.0)
        in_price = float(item.get("in_price") or 0)
        out_price = float(item.get("out_price") or 0)
        stock_amount = cur_qty * in_price

        month_keys = month_keys_range(self.as_of, cfg.analysis_months)
        monthly_out = [agg["monthly_out_qty"].get(k, 0.0) for k in month_keys]
        monthly_out_amt = [agg["monthly_out_amt"].get(k, 0.0) for k in month_keys]

        # ----- 평균재고 & 회전율 -----
        balances = self._month_end_balances(code, agg)
        avg_stock_qty = sum(balances) / len(balances) if balances else 0.0
        annual_factor = 365.0 / self.period_days
        out_qty = agg["out_qty"]
        out_amount = agg["out_amount"]
        if avg_stock_qty > 0:
            turnover = out_qty * annual_factor / avg_stock_qty
        else:
            turnover = None  # 재고 이력이 없으면 회전율 정의 불가
        avg_stock_amt = avg_stock_qty * in_price
        turnover_amt = (out_amount * annual_factor / avg_stock_amt) if avg_stock_amt > 0 else None

        doi_days = (365.0 / turnover) if turnover and turnover > 0 else None

        # ----- 최근 수요 통계(소진예상·안전재고용) -----
        window = min(cfg.recent_window_days,
                     (self.as_of - self.period_start).days + 1)
        daily_map = agg["daily_out_recent"]
        daily_series = []
        for i in range(window):
            d = self.as_of - timedelta(days=window - 1 - i)
            daily_series.append(daily_map.get(d.isoformat(), 0.0))
        avg_daily = sum(daily_series) / window if window > 0 else 0.0
        std_daily = statistics.pstdev(daily_series) if len(daily_series) > 1 else 0.0

        days_to_stockout = (cur_qty / avg_daily) if avg_daily > 0 else None

        # ----- 안전재고 · 재주문점 -----
        z = z_for_service_level(cfg.service_level)
        safety_stock = z * std_daily * math.sqrt(cfg.lead_time_days)
        reorder_point = avg_daily * cfg.lead_time_days + safety_stock
        need_reorder = avg_daily > 0 and cur_qty <= reorder_point
        # 권장발주량: 30일분 수요 + 안전재고 - 현재고
        suggested_order = max(math.ceil(avg_daily * 30 + safety_stock - cur_qty), 0) if avg_daily > 0 else 0

        # ----- 체화 등급 -----
        last_out: date | None = agg["last_out"]
        if last_out is not None:
            days_since_out = (self.as_of - last_out).days
        else:
            # 기간 내 출고 전무: 데이터 시작점(또는 첫 거래일)부터 경과한 것으로 본다
            anchor = agg["first_tx"] or self.period_start
            days_since_out = (self.as_of - max(anchor, self.period_start)).days
        has_stock = cur_qty > 0
        if not has_stock:
            grade = GRADE_NORMAL  # 재고가 없으면 체화 아님
        elif days_since_out >= cfg.dead_days:
            grade = GRADE_DEAD
        elif days_since_out >= cfg.stagnant_days:
            grade = GRADE_STAGNANT
        elif days_since_out >= cfg.caution_days:
            grade = GRADE_CAUTION
        else:
            grade = GRADE_NORMAL

        # ----- 판매 추세 -----
        # 진행 중인 달(데이터가 일부만 쌓인 달)은 비교에서 제외해
        # 월초에 전부 '하락'으로 잘못 판정되는 것을 막는다.
        next_month_start = shift_month(self.as_of, 1)
        is_month_complete = (next_month_start - timedelta(days=1)) == self.as_of
        complete_months = monthly_out if is_month_complete else monthly_out[:-1]
        recent3 = sum(complete_months[-3:])
        prev3 = sum(complete_months[-6:-3])
        if out_qty <= 0:
            trend = TREND_NONE
            growth = None
        elif prev3 <= 0:
            trend = TREND_UP if recent3 > 0 else TREND_NONE
            growth = None
        else:
            growth = (recent3 - prev3) / prev3
            if growth > 0.10:
                trend = TREND_UP
            elif growth < -0.10:
                trend = TREND_DOWN
            else:
                trend = TREND_FLAT
        slope6 = least_squares_slope(complete_months[-6:])

        # ----- 교차비율 -----
        margin_rate = ((out_price - in_price) / out_price) if out_price > 0 else None
        cross_ratio = (turnover_amt * margin_rate) if (turnover_amt is not None and margin_rate is not None) else None

        return {
            "code": code,
            "name": item.get("name") or code,
            "spec": item.get("spec") or "",
            "unit": item.get("unit") or "",
            "category": item.get("category") or "(미분류)",
            "in_price": round(in_price, 2),
            "out_price": round(out_price, 2),
            "cur_qty": round(cur_qty, 3),
            "stock_amount": round(stock_amount),
            "out_qty": round(out_qty, 3),
            "out_amount": round(out_amount),
            "avg_stock_qty": round(avg_stock_qty, 2),
            "turnover": round(turnover, 2) if turnover is not None else None,
            "turnover_amt": round(turnover_amt, 2) if turnover_amt is not None else None,
            "doi_days": round(doi_days, 1) if doi_days is not None else None,
            "avg_daily_out": round(avg_daily, 3),
            "days_to_stockout": round(days_to_stockout, 1) if days_to_stockout is not None else None,
            "safety_stock": round(safety_stock, 1),
            "reorder_point": round(reorder_point, 1),
            "need_reorder": need_reorder,
            "suggested_order": suggested_order,
            "last_out_date": last_out.isoformat() if last_out else None,
            "days_since_out": days_since_out,
            "stagnant_grade": grade,
            "trend": trend,
            "growth_3m": round(growth, 4) if growth is not None else None,
            "slope_6m": round(slope6, 3),
            "monthly_out_qty": [round(v, 2) for v in monthly_out],
            "monthly_out_amt": [round(v) for v in monthly_out_amt],
            "margin_rate": round(margin_rate, 4) if margin_rate is not None else None,
            "cross_ratio": round(cross_ratio, 2) if cross_ratio is not None else None,
        }

    # ------------------------------------------------------------------
    def _assign_abc(self, rows: list[dict]) -> None:
        """기간 출고금액 기준 누적 점유율로 A/B/C 부여(매출 없으면 C)."""
        total = sum(r["out_amount"] for r in rows)
        ordered = sorted(rows, key=lambda r: r["out_amount"], reverse=True)
        cum = 0.0
        for r in ordered:
            if total <= 0 or r["out_amount"] <= 0:
                r["abc"] = "C"
                r["cum_share"] = None
                continue
            cum += r["out_amount"]
            share = cum / total
            r["cum_share"] = round(share, 4)
            if share <= self.cfg.abc_a_cut:
                r["abc"] = "A"
            elif share <= self.cfg.abc_b_cut:
                r["abc"] = "B"
            else:
                r["abc"] = "C"
        # 최상위 1개 품목이 단독으로 80%를 넘는 경우도 A가 되도록 보정
        if ordered and total > 0 and ordered[0]["out_amount"] > 0:
            ordered[0]["abc"] = "A"

    # ------------------------------------------------------------------
    def _recommend(self, r: dict) -> str:
        if r["need_reorder"] and r["abc"] == "A":
            return "긴급 발주 검토"
        if r["need_reorder"]:
            return "발주 검토"
        if r["stagnant_grade"] == GRADE_DEAD:
            return "처분·할인 검토"
        if r["stagnant_grade"] == GRADE_STAGNANT and r["abc"] == "C":
            return "재고 축소 검토"
        if r["trend"] == TREND_UP and r["days_to_stockout"] is not None \
                and r["days_to_stockout"] < self.cfg.lead_time_days * 3:
            return "재고 확충 검토"
        if r["trend"] == TREND_DOWN and r["doi_days"] is not None and r["doi_days"] > 120:
            return "입고 축소 검토"
        return ""

    # ------------------------------------------------------------------
    def _summarize(self, rows: list[dict]) -> dict[str, Any]:
        month_keys = month_keys_range(self.as_of, self.cfg.analysis_months)
        monthly_qty = [0.0] * len(month_keys)
        monthly_amt = [0.0] * len(month_keys)
        for r in rows:
            for i, v in enumerate(r["monthly_out_qty"]):
                monthly_qty[i] += v
            for i, v in enumerate(r["monthly_out_amt"]):
                monthly_amt[i] += v

        total_stock_amount = sum(r["stock_amount"] for r in rows)
        total_out_amount = sum(r["out_amount"] for r in rows)
        # 전사 가중 회전율: 총 출고금액(연환산) ÷ 총 평균재고금액
        total_avg_stock_amt = sum(r["avg_stock_qty"] * r["in_price"] for r in rows)
        annual_factor = 365.0 / self.period_days
        overall_turnover = (total_out_amount * annual_factor / total_avg_stock_amt) \
            if total_avg_stock_amt > 0 else None

        stagnant_rows = [r for r in rows if r["stagnant_grade"] in (GRADE_STAGNANT, GRADE_DEAD)]
        dead_rows = [r for r in rows if r["stagnant_grade"] == GRADE_DEAD]
        stagnant_amount = sum(r["stock_amount"] for r in stagnant_rows)
        dead_amount = sum(r["stock_amount"] for r in dead_rows)

        abc_counts = {"A": 0, "B": 0, "C": 0}
        abc_amounts = {"A": 0.0, "B": 0.0, "C": 0.0}
        for r in rows:
            abc_counts[r["abc"]] += 1
            abc_amounts[r["abc"]] += r["out_amount"]

        reorder_rows = [r for r in rows if r["need_reorder"]]
        imminent = [r for r in rows
                    if r["days_to_stockout"] is not None
                    and r["days_to_stockout"] <= self.cfg.lead_time_days]

        return {
            "item_count": len(rows),
            "total_stock_amount": round(total_stock_amount),
            "total_out_amount": round(total_out_amount),
            "overall_turnover": round(overall_turnover, 2) if overall_turnover is not None else None,
            "stagnant_count": len(stagnant_rows),
            "stagnant_amount": round(stagnant_amount),
            "stagnant_ratio": round(stagnant_amount / total_stock_amount, 4) if total_stock_amount > 0 else 0,
            "dead_count": len(dead_rows),
            "dead_amount": round(dead_amount),
            "reorder_count": len(reorder_rows),
            "imminent_stockout_count": len(imminent),
            "abc_counts": abc_counts,
            "abc_amounts": {k: round(v) for k, v in abc_amounts.items()},
            "monthly_out_qty": [round(v, 1) for v in monthly_qty],
            "monthly_out_amt": [round(v) for v in monthly_amt],
            "trend_up_count": sum(1 for r in rows if r["trend"] == TREND_UP),
            "trend_down_count": sum(1 for r in rows if r["trend"] == TREND_DOWN),
            "no_sales_count": sum(1 for r in rows if r["trend"] == TREND_NONE),
        }
