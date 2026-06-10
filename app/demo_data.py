"""데모용 샘플 데이터 생성기.

ECOUNT 인증키 없이도 대시보드를 확인할 수 있도록,
실제 유통업체와 유사한 패턴(효자품목/계절품목/성장·하락품목/악성재고)을
가진 가상의 입출고 데이터를 생성한다. 시드 고정으로 항상 같은 데이터가 나온다.
"""
from __future__ import annotations

import random
from datetime import date, timedelta
from typing import Any

# (카테고리, 품목명 목록)
CATALOG = [
    ("전자부품", ["리튬배터리 18650", "DC모터 12V", "아두이노 호환보드", "LED바 5050",
                "전원어댑터 12V2A", "릴레이모듈 4CH", "온습도센서 DHT22", "점퍼케이블 세트"]),
    ("생활용품", ["주방세제 1L", "극세사 타월 5P", "밀폐용기 set", "욕실화 PVC",
                "빨래건조대 대형", "쓰레기봉투 100L", "고무장갑 중", "물티슈 100매"]),
    ("사무용품", ["A4용지 박스", "모나미펜 12P", "스테이플러 33호", "포스트잇 5색",
                "클리어파일 A4", "라벨지 21칸", "건전지 AA 10P", "마우스패드"]),
    ("포장자재", ["에어캡 50m", "박스테이프 50개입", "택배박스 3호", "택배박스 5호",
                "PE봉투 대", "완충재 스티로폼", "스트레치필름", "OPP테이프 투명"]),
    ("공구류", ["전동드릴 세트", "육각렌치 9P", "니퍼 6인치", "줄자 5m",
              "커터칼 대형", "실리콘건", "장도리", "드라이버세트 32P"]),
    ("식품류", ["믹스커피 100T", "녹차티백 100T", "종이컵 1000개", "설탕 3kg",
              "생수 2L 6입", "라면 박스", "꿀스틱 30P", "시리얼 600g"]),
]

# 판매 중단 패턴의 중단 시점(전체 기간 대비 진행률)
STOP_CUTOFF = {"dead": 0.25, "stagnating": 0.70}


def _pattern_deck(category_idx: int, rng: random.Random) -> list[str]:
    """카테고리(8개 품목)마다 다양한 패턴이 골고루 섞이도록 보장한다.

    dead(악성: 오래전 판매 중단)와 stagnating(체화: 수개월 전 판매 중단)을
    번갈아 배치해 데모에서도 체화/악성 재고가 반드시 나타나게 한다.
    """
    deck = ["fast", "fast", "steady", "steady", "seasonal", "growing", "declining",
            "dead" if category_idx % 2 == 0 else "stagnating"]
    rng.shuffle(deck)
    return deck


def _daily_demand(pattern: str, base: float, day_idx: int, total_days: int,
                  d: date, rng: random.Random) -> float:
    """패턴별 일별 수요 기대값."""
    progress = day_idx / max(total_days - 1, 1)
    mult = 1.0
    if pattern == "fast":
        mult = 1.6
    elif pattern == "slow":
        mult = 0.25
    elif pattern == "growing":
        mult = 0.4 + 1.6 * progress
    elif pattern == "declining":
        mult = 1.8 - 1.5 * progress
    elif pattern == "seasonal":
        # 여름(6~8월) 성수기 가정
        peak = 1.9 if d.month in (6, 7, 8) else (1.2 if d.month in (5, 9) else 0.55)
        mult = peak
    elif pattern in STOP_CUTOFF:
        mult = 0.9 if progress < STOP_CUTOFF[pattern] else 0.0
    lam = base * mult
    # 주말 수요 감소
    if d.weekday() >= 5:
        lam *= 0.35
    # 포아송 근사: 작은 lambda는 확률적 0/1, 큰 값은 정규 근사
    if lam <= 0:
        return 0.0
    if lam < 1.5:
        return float(rng.random() < lam) * max(1, round(rng.gauss(lam * 2, 1)))
    return max(0.0, rng.gauss(lam, lam * 0.45))


def generate_demo_dataset(as_of: date | None = None, months: int = 13,
                          seed: int = 42) -> dict[str, Any]:
    rng = random.Random(seed)
    as_of = as_of or date.today()
    start = as_of - timedelta(days=months * 31)
    total_days = (as_of - start).days + 1

    items: list[dict] = []
    transactions: list[dict] = []
    stock: list[dict] = []

    idx = 0
    for cat_idx, (category, names) in enumerate(CATALOG):
        deck = _pattern_deck(cat_idx, rng)
        for name_idx, name in enumerate(names):
            idx += 1
            code = f"P{idx:04d}"
            pattern = deck[name_idx % len(deck)]
            base = rng.uniform(1.0, 9.0)
            in_price = round(rng.uniform(800, 45000), -2)
            margin = rng.uniform(0.18, 0.45)
            out_price = round(in_price / (1 - margin), -2)
            items.append({
                "code": code, "name": name, "spec": rng.choice(["", "BOX", "EA", "SET"]),
                "unit": rng.choice(["EA", "BOX", "SET"]),
                "category": category,
                "in_price": in_price, "out_price": out_price,
            })

            # 일자별 시뮬레이션: 수요 발생 → 재고 부족 시 입고(리드타임 반영)
            qty = round(base * rng.uniform(25, 45))  # 기초재고
            pending: list[tuple[date, float]] = []   # (입고예정일, 수량)
            reorder_level = base * 14
            order_qty = max(round(base * 35), 10)
            for i in range(total_days):
                d = start + timedelta(days=i)
                # 예정 입고 반영
                arrived = [p for p in pending if p[0] <= d]
                for _, q in arrived:
                    qty += q
                    transactions.append({"date": d.isoformat(), "code": code,
                                         "io": "IN", "qty": q,
                                         "amount": round(q * in_price)})
                pending = [p for p in pending if p[0] > d]

                demand = _daily_demand(pattern, base, i, total_days, d, rng)
                sold = min(qty, round(demand))
                if sold > 0:
                    qty -= sold
                    transactions.append({"date": d.isoformat(), "code": code,
                                         "io": "OUT", "qty": sold,
                                         "amount": round(sold * out_price)})
                # 발주 판단: 판매 중단 패턴은 중단 시점 이후 발주하지 않아
                # 남은 재고가 체화/악성으로 굳는다
                stopped = pattern in STOP_CUTOFF and i / total_days >= STOP_CUTOFF[pattern]
                if qty + sum(q for _, q in pending) < reorder_level and not stopped:
                    lead = rng.randint(3, 10)
                    pending.append((d + timedelta(days=lead), order_qty))

            # 판매 중단 품목인데 재고가 다 소진됐으면 잔여재고를 남겨 체화를 재현한다
            if pattern in STOP_CUTOFF and qty < 5:
                qty += round(base * 20)
            stock.append({"code": code, "qty": qty})

    return {
        "as_of": as_of.isoformat(),
        "source": "demo",
        "items": items,
        "stock": stock,
        "transactions": transactions,
    }
