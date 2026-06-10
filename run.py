"""대시보드 실행 스크립트.

    python run.py            # http://localhost:8000 에서 실행 + 브라우저 자동 오픈
    python run.py --port 8080
"""
import argparse
import threading
import webbrowser

import uvicorn


def main():
    parser = argparse.ArgumentParser(description="ECOUNT 재고 분석 대시보드")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-browser", action="store_true", help="브라우저 자동 실행 안 함")
    args = parser.parse_args()

    url = f"http://{'localhost' if args.host in ('0.0.0.0', '127.0.0.1') else args.host}:{args.port}"
    print(f"\n  재고 분석 대시보드: {url}\n  종료하려면 Ctrl+C\n")

    if not args.no_browser:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()

    uvicorn.run("app.main:app", host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
