"""APScheduler 기반 자동 스케줄러 — 48~72시간(기본 60시간) 간격으로 파이프라인을
[수집 -> AI 파싱 -> DB 저장 -> 카드뉴스 렌더링] 순서로 자동 반복 실행한다.

실행 (계속 떠 있어야 함, Ctrl+C로 종료):
    python -m scheduler.cron_job

즉시 1회만 실행하고 종료하고 싶으면 (테스트용):
    python -m scheduler.cron_job --once
"""
from __future__ import annotations

import logging
import sys

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger

from config import CRON_INTERVAL_HOURS
from scheduler.pipeline import run_pipeline_once

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def main() -> None:
    if "--once" in sys.argv:
        run_pipeline_once()
        return

    scheduler = BlockingScheduler(timezone="Asia/Seoul")
    scheduler.add_job(
        run_pipeline_once,
        trigger=IntervalTrigger(hours=CRON_INTERVAL_HOURS),
        id="gonggu_pipeline",
        max_instances=1,
        coalesce=True,
    )
    logger.info("자동 스케줄러 시작: %d시간 주기로 실행 (Ctrl+C로 종료)", CRON_INTERVAL_HOURS)

    # 시작하자마자 1회 즉시 실행 후, 이후로는 위 주기를 따른다.
    run_pipeline_once()

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("스케줄러 종료")


if __name__ == "__main__":
    main()
