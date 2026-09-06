"""자동 발굴 -> 수집 -> 파싱 -> 카드뉴스 생성 파이프라인을 한 번 실행.

run_manual.py(수동 실행)와 scheduler/cron_job.py(자동 스케줄러)가 이 함수 하나를
공유한다 — 파이프라인 순서/오류 처리를 바꿔야 하면 이 파일만 고치면 된다.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def run_pipeline_once() -> dict:
    from scraper.discover import discover
    from scraper.collector import run as run_scrape
    from parser.extract_schedule import run as run_parse
    from generator.card_news import run as run_generate

    result: dict = {
        "discover_targets": 0,
        "scrape_targets": 0, "scrape_ok": False,
        "parse_stats": None, "card_paths": [],
    }

    logger.info("=== 파이프라인 시작 ===")

    try:
        discovered = discover()
        result["discover_targets"] = len(discovered)
    except Exception:
        logger.exception("자동 발굴 단계 실패 - 기존 data/targets.json 으로 계속 진행")

    try:
        scraped = run_scrape()
        result["scrape_ok"] = True
        result["scrape_targets"] = len(scraped)
    except Exception:
        logger.exception("수집 단계 실패 - 이후 단계는 기존 data/raw_collected.json 으로 계속 진행")

    try:
        result["parse_stats"] = run_parse()
    except Exception:
        logger.exception("파싱 단계 실패")

    try:
        result["card_paths"] = run_generate()
    except Exception:
        logger.exception("카드뉴스 생성 단계 실패")

    logger.info("=== 파이프라인 종료 ===")
    return result
