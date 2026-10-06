"""카드뉴스 PNG 없이 사이트 산출물만 다시 만든다 (index.html / view.html / experience_deals.json /
calendar_summary.txt / creators_stats.json / sitemap lastmod).

야간 파이프라인(generator.card_news.run)의 '요약 행 빌드 이후' 부분과 같은 함수를 부른다. DB를 새로 파싱한 뒤
(백필 등) 배포 산출물만 갱신하고 싶을 때 쓴다. 야간 파이프라인이 도는 중에는 실행하지 말 것.

실행:  python tools/rebuild_site_outputs.py
"""
import logging
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from generator import card_news as cn  # noqa: E402


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    today = date.today()
    start, end = cn._current_display_range(today)
    rows = cn._build_summary_rows(start, end, hide_before=today)
    cn._write_calendar_summary_txt(rows, start, end, None)
    cn._write_view_html(rows, start, end, None)
    exp_path = cn._write_experience_json(rows)
    cn._write_creators_stats()
    cn._update_sitemap_lastmod()
    n_exp = sum(1 for r in rows if r.get("item_type") == "experience")
    print(f"요약 행 {len(rows)}건 / 지도 체험 {n_exp}건 -> {exp_path}")


if __name__ == "__main__":
    main()
