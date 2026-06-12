"""환경설정 로딩.

.env 파일 또는 환경변수에서 설정을 읽는다.
ECOUNT 인증키가 없으면 자동으로 데모 모드로 동작한다.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _env_bool(key: str, default: bool) -> bool:
    raw = os.getenv(key)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "y", "on")


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, "").strip())
    except ValueError:
        return default


def _env_float(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, "").strip())
    except ValueError:
        return default


@dataclass
class Settings:
    # ECOUNT Open API 인증 정보
    com_code: str = field(default_factory=lambda: os.getenv("ECOUNT_COM_CODE", "").strip())
    user_id: str = field(default_factory=lambda: os.getenv("ECOUNT_USER_ID", "").strip())
    api_cert_key: str = field(default_factory=lambda: os.getenv("ECOUNT_API_CERT_KEY", "").strip())
    # Zone을 미리 알고 있으면 지정(비우면 자동 조회)
    zone: str = field(default_factory=lambda: os.getenv("ECOUNT_ZONE", "").strip())
    # 테스트 서버(sboapi) 사용 여부 — ECOUNT 테스트 인증키 사용 시 true
    use_test_server: bool = field(default_factory=lambda: _env_bool("ECOUNT_TEST_SERVER", False))
    # 고정 IP 프록시 URL(ECOUNT IP 허용목록 대응). 예: http://user:pass@host:port
    ecount_proxy: str = field(default_factory=lambda: os.getenv("ECOUNT_PROXY", "").strip())

    # 분석 파라미터
    analysis_months: int = field(default_factory=lambda: _env_int("ANALYSIS_MONTHS", 12))
    recent_window_days: int = field(default_factory=lambda: _env_int("RECENT_WINDOW_DAYS", 90))
    lead_time_days: int = field(default_factory=lambda: _env_int("LEAD_TIME_DAYS", 7))
    service_level: float = field(default_factory=lambda: _env_float("SERVICE_LEVEL", 0.95))
    caution_days: int = field(default_factory=lambda: _env_int("STAGNANT_CAUTION_DAYS", 60))
    stagnant_days: int = field(default_factory=lambda: _env_int("STAGNANT_DAYS", 90))
    dead_days: int = field(default_factory=lambda: _env_int("DEAD_DAYS", 180))
    abc_a_cut: float = field(default_factory=lambda: _env_float("ABC_A_CUT", 0.80))
    abc_b_cut: float = field(default_factory=lambda: _env_float("ABC_B_CUT", 0.95))

    # 데모 모드 강제 여부(미설정 시 인증키 없으면 데모)
    demo_mode_env: str = field(default_factory=lambda: os.getenv("DEMO_MODE", "").strip().lower())

    # 대시보드 접근 비밀번호(공개 배포 시 실데이터 보호용, 실데이터 모드에선 필수)
    dashboard_password: str = field(default_factory=lambda: os.getenv("DASHBOARD_PASSWORD", "").strip())

    # 서버리스(Vercel)는 프로젝트 디렉터리가 읽기전용이라 /tmp에 캐시한다
    cache_path: Path = field(default_factory=lambda: (
        Path("/tmp/erp_cache.json") if os.getenv("VERCEL")
        else BASE_DIR / "data" / "cache.json"
    ))

    @property
    def has_credentials(self) -> bool:
        return bool(self.com_code and self.user_id and self.api_cert_key)

    @property
    def demo_mode(self) -> bool:
        if self.demo_mode_env in ("1", "true", "yes", "on"):
            return True
        if self.demo_mode_env in ("0", "false", "no", "off"):
            return False
        return not self.has_credentials


settings = Settings()
