"""Vercel 서버리스 진입점.

FastAPI ASGI 앱(app.main:app)을 그대로 노출한다. Vercel의 Python 런타임이
모듈 수준의 `app` 변수를 ASGI 애플리케이션으로 인식해 서빙한다.
모든 경로(/, /static/*, /api/*)는 vercel.json의 rewrite로 이 함수에 매핑된다.
"""
import sys
from pathlib import Path

# 이 파일은 api/ 하위에서 실행되므로 프로젝트 루트를 import 경로에 추가한다.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.main import app  # noqa: E402  (ASGI 핸들러로 노출)

__all__ = ["app"]
