"""분석 엔진 검증 테스트 (표준 라이브러리 unittest).

    python -m unittest discover tests -v
"""
import unittest
from dataclasses import dataclass

from app.analysis import AnalysisEngine, month_keys_range, parse_date


@dataclass
class Cfg:
    analysis_months: int = 12
    recent_window_days: int = 90
    lead_time_days: int = 7
    service_level: float = 0.95
    caution_days: int = 60
    stagnant_days: int = 90
    dead_days: int = 180
    abc_a_cut: float = 0.80
    abc_b_cut: float = 0.95


def make_dataset(**over):
    base = {
        "as_of": "2026-06-10",
        "items": [
            {"code": "A1", "name": "잘나가는품목", "spec": "", "unit": "EA",
             "category": "테스트", "in_price": 1000, "out_price": 2000},
            {"code": "B1", "name": "악성품목", "spec": "", "unit": "EA",
             "category": "테스트", "in_price": 5000, "out_price": 8000},
        ],
        "stock": [{"code": "A1", "qty": 50}, {"code": "B1", "qty": 100}],
        "transactions": [],
    }
    base.update(over)
    return base


def steady_sales(code, qty_per_day, months=12, end="2026-06-10"):
    """매일 일정 수량 출고 거래 생성."""
    from datetime import timedelta
    end_d = parse_date(end)
    txs = []
    for i in range(months * 30):
        d = end_d - timedelta(days=i)
        txs.append({"date": d.isoformat(), "code": code, "io": "OUT",
                    "qty": qty_per_day, "amount": qty_per_day * 2000})
    return txs


class TestHelpers(unittest.TestCase):
    def test_month_keys_range(self):
        keys = month_keys_range(parse_date("2026-06-10"), 3)
        self.assertEqual(keys, ["2026-04", "2026-05", "2026-06"])


class TestEngine(unittest.TestCase):
    def run_engine(self, dataset, cfg=None):
        return AnalysisEngine(dataset, cfg or Cfg()).run()

    def test_empty_dataset(self):
        result = self.run_engine(make_dataset(items=[], stock=[], transactions=[]))
        self.assertEqual(result["summary"]["item_count"], 0)
        self.assertEqual(result["summary"]["total_stock_amount"], 0)

    def test_stock_amount(self):
        result = self.run_engine(make_dataset())
        rows = {r["code"]: r for r in result["items"]}
        self.assertEqual(rows["A1"]["stock_amount"], 50 * 1000)
        self.assertEqual(rows["B1"]["stock_amount"], 100 * 5000)
        self.assertEqual(result["summary"]["total_stock_amount"], 50000 + 500000)

    def test_turnover_and_doi(self):
        # 매일 2개씩 출고, 현재고 50 → 회전율이 높고 재고일수가 짧아야 함
        ds = make_dataset(transactions=steady_sales("A1", 2))
        result = self.run_engine(ds)
        a1 = next(r for r in result["items"] if r["code"] == "A1")
        self.assertIsNotNone(a1["turnover"])
        self.assertGreater(a1["turnover"], 1)
        self.assertIsNotNone(a1["doi_days"])
        self.assertAlmostEqual(a1["doi_days"], 365 / a1["turnover"], delta=0.5)
        # 일평균 출고 ≈ 2 → 소진예상 ≈ 25일
        self.assertAlmostEqual(a1["avg_daily_out"], 2.0, delta=0.01)
        self.assertAlmostEqual(a1["days_to_stockout"], 25.0, delta=0.5)

    def test_dead_stock_grade(self):
        # B1: 거래가 전혀 없고 재고만 보유 → 악성
        ds = make_dataset(transactions=steady_sales("A1", 2))
        result = self.run_engine(ds)
        b1 = next(r for r in result["items"] if r["code"] == "B1")
        self.assertEqual(b1["stagnant_grade"], "악성")
        self.assertEqual(b1["trend"], "판매없음")
        self.assertIn(b1["abc"], ("C",))
        # 재고가 없는 무판매 품목은 체화 아님
        ds2 = make_dataset(transactions=steady_sales("A1", 2),
                           stock=[{"code": "A1", "qty": 50}, {"code": "B1", "qty": 0}])
        result2 = self.run_engine(ds2)
        b1_2 = next(r for r in result2["items"] if r["code"] == "B1")
        self.assertEqual(b1_2["stagnant_grade"], "정상")

    def test_abc_assignment(self):
        ds = make_dataset(transactions=steady_sales("A1", 2))
        result = self.run_engine(ds)
        a1 = next(r for r in result["items"] if r["code"] == "A1")
        self.assertEqual(a1["abc"], "A")  # 매출 100% 차지

    def test_reorder_point(self):
        # 매일 2개 출고 / 리드타임 7일 → ROP ≥ 14, 현재고 5면 발주 필요
        ds = make_dataset(transactions=steady_sales("A1", 2),
                          stock=[{"code": "A1", "qty": 5}, {"code": "B1", "qty": 100}])
        result = self.run_engine(ds)
        a1 = next(r for r in result["items"] if r["code"] == "A1")
        self.assertGreaterEqual(a1["reorder_point"], 14)
        self.assertTrue(a1["need_reorder"])
        self.assertGreater(a1["suggested_order"], 0)
        self.assertIn("발주", a1["action"])

    def test_trend_up(self):
        # 직전 3개월 일1개 → 최근 3개월 일4개 출고
        from datetime import timedelta
        end = parse_date("2026-06-10")
        txs = []
        for i in range(180):
            d = end - timedelta(days=i)
            qty = 4 if i < 90 else 1
            txs.append({"date": d.isoformat(), "code": "A1", "io": "OUT",
                        "qty": qty, "amount": qty * 2000})
        result = self.run_engine(make_dataset(transactions=txs))
        a1 = next(r for r in result["items"] if r["code"] == "A1")
        self.assertEqual(a1["trend"], "상승")
        self.assertGreater(a1["growth_3m"], 0.5)

    def test_cross_ratio(self):
        ds = make_dataset(transactions=steady_sales("A1", 2))
        result = self.run_engine(ds)
        a1 = next(r for r in result["items"] if r["code"] == "A1")
        # 마진율 = (2000-1000)/2000 = 0.5
        self.assertAlmostEqual(a1["margin_rate"], 0.5)
        self.assertIsNotNone(a1["cross_ratio"])
        self.assertAlmostEqual(a1["cross_ratio"], a1["turnover_amt"] * 0.5, delta=0.6)

    def test_unknown_code_in_transactions(self):
        # 품목 마스터에 없는 코드가 거래에 있어도 죽지 않아야 함
        ds = make_dataset(transactions=[
            {"date": "2026-06-01", "code": "GHOST", "io": "OUT", "qty": 3, "amount": 300},
        ])
        result = self.run_engine(ds)
        codes = {r["code"] for r in result["items"]}
        self.assertIn("GHOST", codes)


class TestDemoData(unittest.TestCase):
    def test_demo_generation_deterministic(self):
        from app.demo_data import generate_demo_dataset
        from datetime import date
        d1 = generate_demo_dataset(as_of=date(2026, 6, 10))
        d2 = generate_demo_dataset(as_of=date(2026, 6, 10))
        self.assertEqual(len(d1["items"]), len(d2["items"]))
        self.assertEqual(d1["transactions"][:50], d2["transactions"][:50])
        self.assertGreater(len(d1["items"]), 40)
        self.assertGreater(len(d1["transactions"]), 1000)

    def test_demo_analysis_runs(self):
        from app.demo_data import generate_demo_dataset
        from datetime import date
        ds = generate_demo_dataset(as_of=date(2026, 6, 10))
        result = AnalysisEngine(ds, Cfg()).run()
        s = result["summary"]
        self.assertGreater(s["total_stock_amount"], 0)
        self.assertGreater(s["total_out_amount"], 0)
        self.assertIsNotNone(s["overall_turnover"])
        # 데모 데이터에는 악성재고가 반드시 포함되어야 함
        self.assertGreater(s["stagnant_count"], 0)
        # 모든 품목에 등급이 부여되어야 함
        for r in result["items"]:
            self.assertIn(r["abc"], ("A", "B", "C"))
            self.assertIn(r["stagnant_grade"], ("정상", "주의", "체화", "악성"))


if __name__ == "__main__":
    unittest.main()
