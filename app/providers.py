"""데이터 공급자: 데모 / ECOUNT 실연동 + 로컬 캐시.

대시보드 로딩 때마다 ECOUNT API를 때리지 않도록 수집 결과를
data/cache.json에 저장하고, '데이터 새로고침'을 누를 때만 재수집한다.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from . import demo_data, ecount_client
from .config import Settings


def load_cache(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        with path.open(encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def save_cache(path: Path, dataset: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            json.dump(dataset, f, ensure_ascii=False)
    except OSError:
        # 읽기전용 파일시스템(서버리스 등)에서는 캐시 저장을 건너뛴다
        pass


def fetch_dataset(settings: Settings, force_refresh: bool = False) -> dict[str, Any]:
    """캐시가 있으면 캐시를, 없거나 새로고침 요청이면 원천에서 수집."""
    if not force_refresh:
        cached = load_cache(settings.cache_path)
        # 모드가 바뀌면(데모→실연동 등) 캐시를 무시한다
        expected = "demo" if settings.demo_mode else "ecount"
        if cached and cached.get("source") == expected:
            return cached

    if settings.demo_mode:
        dataset = demo_data.generate_demo_dataset()
    else:
        dataset = ecount_client.fetch_dataset(settings)
    dataset["fetched_at"] = datetime.now().isoformat(timespec="seconds")
    save_cache(settings.cache_path, dataset)
    return dataset
