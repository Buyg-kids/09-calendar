"""CLI 진입점.

    python main.py discover    # 인플루언서 자동 발굴만
    python main.py scrape      # 수집만
    python main.py parse       # 파싱만
    python main.py generate    # 카드뉴스 생성만
    python main.py pipeline    # 발굴->수집->파싱->생성 1회 전체 실행
    python main.py serve       # 웹 대시보드 실행 (uvicorn)
"""
from __future__ import annotations

import sys


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(1)

    cmd = sys.argv[1]

    if cmd == "discover":
        from scraper.discover import discover
        discover()
    elif cmd == "scrape":
        from scraper.collector import run
        run()
    elif cmd == "parse":
        from parser.extract_schedule import run
        run()
    elif cmd == "generate":
        from generator.card_news import run
        run()
    elif cmd == "pipeline":
        from scheduler.pipeline import run_pipeline_once
        run_pipeline_once()
    elif cmd == "serve":
        import uvicorn
        uvicorn.run("web.main:app", host="127.0.0.1", port=8000, reload=True)
    else:
        print(f"알 수 없는 명령: {cmd}")
        print(__doc__)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
