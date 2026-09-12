"""수동 실행 진입점.

터미널에서 한 번 실행하면 [인플루언서 자동 발굴 -> 멀티링크 데이터 수집 ->
룰베이스/AI 하이브리드 파싱 -> DB 저장 -> 주간 카드뉴스 렌더링]을 사용자 추가
입력 없이 전부 수행하고, 끝나면 결과 요약을 보여준다. targets.json을 직접
채우지 않아도 되고, .env에 ANTHROPIC_API_KEY가 없어도 끝까지 동작한다
(그 경우 파싱은 정규식/키워드 룰베이스로만 수행됨).

실행:
    python run_manual.py
"""
from __future__ import annotations

import logging
import sys
import time

from scheduler.pipeline import run_pipeline_once

# Windows 콘솔이 UTF-8이 아닌 코드페이지(cp949 등)일 때 print()가 깨지는 것을 방지
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def main() -> None:
    print("=" * 60)
    print("영유아 공구 캘린더 파이프라인 - 자동 발굴 + 수동 실행")
    print("=" * 60)

    started = time.time()
    result = run_pipeline_once()
    elapsed = time.time() - started

    stats = result.get("parse_stats") or {}
    image_stats = result.get("image_fallback_stats") or {}
    cards = result.get("card_paths") or []

    print("\n" + "=" * 60)
    print(f"완료 ({elapsed:.1f}초 소요)")
    print(f"  1) 자동 발굴 : 타겟 {result['discover_targets']}명 발굴 -> data/targets.json 갱신")
    print(f"  2) 수집     : {'성공' if result['scrape_ok'] else '실패 (로그 확인)'} "
          f"- 타겟 {result['scrape_targets']}명")
    print(f"  3) 파싱     : 검사 {stats.get('blobs_checked', 0)}건 / "
          f"Claude 호출 {stats.get('blobs_sent_to_claude', 0)}건 / "
          f"gonggu.db 저장 {stats.get('saved', 0)}건 "
          f"(룰베이스 {stats.get('saved_by_rule', 0)} + Claude {stats.get('saved_by_claude', 0)}) / "
          f"날짜 불명 스킵 {stats.get('skipped_no_date', 0)}건")
    if image_stats.get("checked", 0) > 0 or image_stats.get("filled", 0) > 0:
        print(f"  4) 이미지 폴백 : 누락 {image_stats.get('checked', 0)}건 중 "
              f"네이버 쇼핑으로 {image_stats.get('filled', 0)}건 채움 "
              f"(실패 {image_stats.get('failed', 0)}건)")
    else:
        print("  4) 이미지 폴백 : 스킵 (누락 없음 또는 NAVER_CLIENT_ID/SECRET 미설정)")
    if cards:
        print(f"  5) 카드뉴스 : {len(cards)}장 생성")
        for p in cards:
            print(f"       - {p}")
    else:
        print("  5) 카드뉴스 : 생성된 이미지 없음 (이번 주 저장된 공구 데이터가 없는지 확인)")
    print("=" * 60)


if __name__ == "__main__":
    main()
