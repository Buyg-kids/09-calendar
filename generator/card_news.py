"""[Agent 4: 카드뉴스 자동 렌더러]

gonggu.db의 특정 한 주(월~일) 데이터를 인스타그램 업로드용 카드뉴스 4장으로 렌더링한다:
  1장 - 해당 주 육아/식품 공구 라인업 요약 표지
  2장 - [육아용품] 주간 캘린더
  3장 - [영유아식품] 주간 캘린더
  4장 - [키즈가구] 주간 캘린더

기본은 "이번 주"(오늘 날짜 기준)지만, 특정 주를 지정할 수도 있다.
결과는 output/cardnews_YYYYMMDD/ (자동 실행 시 YYYYMMDD=생성일, 특정 주 지정 시
YYYYMMDD=그 주의 월요일 날짜) 에 번호순으로 저장된다:
  1_cover.png, 2_육아용품.png, 3_영유아식품.png, 4_키즈가구.png

같은 실행에서 검색/텍스트 보조 파일 2개도 output/ 바로 아래(고정 경로,
매번 최신 데이터로 덮어씀)에 함께 생성한다:
  output/calendar_summary.txt - 인스타 캡션으로 바로 붙여넣을 수 있는 텍스트 목록
  output/view.html            - 검색창 + Ctrl+F 가능한 단일 HTML 뷰어 (index.html로도 복사됨)

카드뉴스(PNG 4장)는 계속 "이번 주" 기준이지만, calendar_summary.txt/view.html은
당월 1일~말일이 기본이고 매월 15일부터는 다음 달 말일까지 노출 범위를 미리
넓히는 롤링 방식이다(_current_display_range). 이미 마감된(끝난) 공구는 이
두 산출물에서 자동으로 숨겨진다.

실행:
    python -m generator.card_news                              # 이번 주
    python -m generator.card_news 2026-09-07                   # 해당 날짜가 속한 주
    python -m generator.card_news 2026-09-07 "9월 2주차 육아&식품 공구 캘린더"
"""
from __future__ import annotations

import calendar as calendar_mod
import json
import logging
import re
from datetime import date, datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path

import gonggu_db
from config import BASE_DIR, CATEGORIES, CATEGORY_NAMES, OUTPUT_DIR, TARGETS_PATH
from generator.card_renderer import render_card_html, render_card_png

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

WEEKDAY_KR = ["월", "화", "수", "목", "금", "토", "일"]
HIGHLIGHTS_PER_CATEGORY = 3


def _current_week_range(today: date | None = None) -> tuple[date, date]:
    today = today or date.today()
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)
    return monday, sunday


def _current_display_range(today: date | None = None) -> tuple[date, date]:
    """view.html/calendar_summary.txt 용 노출 기간(당월 + 익월 D-14 롤링).

    기본은 이번 달 1일 ~ 말일. 다만 매월 15일부터는 다음 달 공구 예고가
    슬슬 올라오기 시작하므로, 노출 범위를 다음 달 말일까지 미리 넓혀서
    (예: 9/15부터는 9월 전체 + 10월 전체) 놓치는 일정이 없게 한다.
    카드뉴스(PNG) 4장은 인스타그램 피드용으로 여전히 '이번 주' 기준을 그대로
    쓴다 - 이 함수는 view.html/calendar_summary.txt 전용."""
    today = today or date.today()
    start = today.replace(day=1)

    end_year, end_month = today.year, today.month
    if today.day >= 15:
        end_month += 1
        if end_month > 12:
            end_month = 1
            end_year += 1

    last_day = calendar_mod.monthrange(end_year, end_month)[1]
    end = date(end_year, end_month, last_day)
    return start, end


def _parse_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return None


def _bucket_by_day(items: list[dict], monday: date) -> dict[int, list[dict]]:
    buckets: dict[int, list[dict]] = {i: [] for i in range(7)}
    for gb in items:
        start = _parse_date(gb.get("start_date")) or _parse_date(gb.get("end_date"))
        end = _parse_date(gb.get("end_date")) or start
        if not start:
            continue
        for i in range(7):
            day = monday + timedelta(days=i)
            if start <= day <= end:
                buckets[i].append(gb)
    return buckets


def _build_category_context(category: str, monday: date, sunday: date, page_info: str) -> dict:
    items = gonggu_db.list_gonggu(start_date=monday.isoformat(), end_date=sunday.isoformat(), category=category)
    buckets = _bucket_by_day(items, monday)

    days = []
    for i in range(7):
        d = monday + timedelta(days=i)
        entries = [
            {
                "product_name": gb["product_name"],
                "influencer_name": gb["influencer_name"],
                "brand": gb.get("brand") or "",
                "key_benefit": gb.get("key_benefit") or "",
            }
            for gb in buckets[i]
        ]
        days.append({"weekday_kr": WEEKDAY_KR[i], "date_num": d.day, "entries": entries})

    return {
        "category": category,
        "color": CATEGORIES[category]["color"],
        "range_text": f"{monday.strftime('%m.%d')} ~ {sunday.strftime('%m.%d')}",
        "page_info": page_info,
        "days": days,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }


def _build_cover_context(monday: date, sunday: date, title: str | None = None) -> dict:
    sections = []
    total_count = 0
    for name in CATEGORY_NAMES:
        items = gonggu_db.list_gonggu(start_date=monday.isoformat(), end_date=sunday.isoformat(), category=name)
        items.sort(key=lambda gb: gb.get("start_date") or "9999-99-99")
        total_count += len(items)
        sections.append(
            {
                "category": name,
                "color": CATEGORIES[name]["color"],
                "count": len(items),
                "highlights": [it["product_name"] for it in items[:HIGHLIGHTS_PER_CATEGORY]],
            }
        )

    return {
        "title": title or "이번 주 육아/식품 공구 라인업",
        "range_text": f"{monday.strftime('%Y.%m.%d')} ~ {sunday.strftime('%m.%d')}",
        "total_count": total_count,
        "sections": sections,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }


_INFLUENCER_RE = re.compile(r"^(.*?)\s*\(([^)]+)\)\s*$")
_PAREN_RE = re.compile(r"\([^)]*\)")
_PRICE_PREFIX_RE = re.compile(r"[\d,]+~?[\d,]*\s*만?원대?")


def _load_multilink_map() -> dict[str, str]:
    """targets.json의 핸들 -> 멀티링크 URL 매핑 (구매링크가 없을 때 fallback용)."""
    if not TARGETS_PATH.exists():
        return {}
    try:
        data = json.loads(TARGETS_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    result = {}
    for t in data:
        handle = (t.get("influencer_name") or "").lstrip("@")
        url = t.get("multilink_url") or ""
        if handle and url:
            result[handle] = url
    return result


def _parse_influencer(raw: str) -> tuple[str, str]:
    """'또니맘 (seohui_jeong)' -> ('또니맘', 'seohui_jeong').
    해시태그로 발굴된 계정은 한글 닉네임 없이 DB에 핸들만 저장되어 있으므로
    (예: 'suksuk_mom_'), 괄호 형식이 아니면 원문 전체를 핸들로 간주한다
    ('', 'suksuk_mom_') - 닉네임 유무와 무관하게 항상 프로필 링크가 걸리도록
    보장하기 위함."""
    raw = (raw or "").strip()
    m = _INFLUENCER_RE.match(raw)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return "", raw


def _influencer_label(display: str, handle: str) -> str:
    """표시용 라벨. 닉네임+핸들 둘 다 있으면 '또니맘 (seohui_jeong)',
    핸들만 있으면 '@suksuk_mom_'."""
    if display and handle:
        return f"{display} ({handle})"
    if handle:
        return f"@{handle}"
    return display


def _normalize_product_name(name: str, brand: str) -> str:
    """'1만원대 어린이한복' / '어린이한복 (금동이한복)' 가 같은 상품으로 잡히도록
    브랜드명, 괄호 표기, 가격대 접두어, 공백을 제거해 비교용 키를 만든다."""
    s = name or ""
    if brand:
        s = s.replace(brand, "")
    s = _PAREN_RE.sub("", s)
    s = _PRICE_PREFIX_RE.sub("", s)
    return re.sub(r"\s+", "", s).strip()


def _dates_overlap(a_start: str, a_end: str, b_start: str, b_end: str) -> bool:
    return a_start <= (b_end or b_start) and b_start <= (a_end or a_start)


def _is_same_product(a: dict, b: dict) -> bool:
    """[동일 인플루언서 + 날짜 겹침]을 전제로, 다음 중 하나면 같은 공구(=같은
    피드에서 나온 파생 옵션 포함)로 본다:
      1) 브랜드가 둘 다 있고 동일함 - 한 피드에서 여러 옵션(예: '무무칩'/
         '무무솔솔')이 별개 상품으로 쪼개져 파싱되는 경우를 잡기 위함.
      2) 정규화한 상품명이 서로 포함 관계이거나 충분히 유사함 - 브랜드 표기가
         다르거나 없는 경우(예: '1만원대 어린이한복' vs '어린이한복(금동이한복)')."""
    if a["influencer_name"] != b["influencer_name"]:
        return False
    if not _dates_overlap(a["start_date"], a.get("end_date") or "", b["start_date"], b.get("end_date") or ""):
        return False

    brand_a = (a.get("brand") or "").strip()
    brand_b = (b.get("brand") or "").strip()
    if brand_a and brand_a == brand_b:
        return True

    # 브랜드가 비어있는 경우(파서가 브랜드를 못 뽑은 경우가 흔함)에도, 같은 피드
    # 글 하나에서 옵션 여러 개가 쪼개져 나오면 보통 안내 문구(혜택)가 토씨 하나
    # 안 틀리고 그대로 반복된다 - 실제로 '무무칩'/'무무솔솔'(suksuk_mom_) 케이스가
    # 이렇게 동일 혜택 문구를 공유해서 발견됨.
    benefit_a = (a.get("key_benefit") or "").strip()
    benefit_b = (b.get("key_benefit") or "").strip()
    if benefit_a and benefit_a == benefit_b:
        return True

    na = _normalize_product_name(a["product_name"], brand_a)
    nb = _normalize_product_name(b["product_name"], brand_b)
    if not na or not nb:
        return False
    if na in nb or nb in na:
        return True
    return SequenceMatcher(None, na, nb).ratio() >= 0.6


def _combine_product_names(names: list[str]) -> str:
    """중복 통합된 그룹의 상품명 표기. 2개면 'A / B', 3개 이상이면 'A 외 N종'."""
    uniq: list[str] = []
    for n in names:
        n = (n or "").strip()
        if n and n not in uniq:
            uniq.append(n)
    if len(uniq) <= 1:
        return uniq[0] if uniq else ""
    if len(uniq) == 2:
        return f"{uniq[0]} / {uniq[1]}"
    return f"{uniq[0]} 외 {len(uniq) - 1}종"


def _merge_duplicate_group(group: list[dict]) -> dict:
    """같은 공구로 판단된 묶음을 1건으로 합친다: 날짜는 합집합(가장 이른 시작 ~
    가장 늦은 끝), 상품명은 'A / B' 또는 'A 외 N종' 형태로 통합, 혜택은 더
    상세한(긴) 쪽을 채택, 구매링크는 실제 http 링크가 있는 항목을 우선한다."""
    base = max(group, key=lambda it: len(it["product_name"] or "") + len(it.get("key_benefit") or ""))
    merged = dict(base)
    merged["start_date"] = min(it["start_date"] for it in group)
    merged["end_date"] = max((it.get("end_date") or it["start_date"]) for it in group)
    merged["key_benefit"] = max((it.get("key_benefit") or "" for it in group), key=len)
    merged["product_name"] = _combine_product_names([it["product_name"] for it in group])
    for it in group:
        link = it.get("purchase_link") or ""
        if link.startswith("http"):
            merged["purchase_link"] = link
            break
    if not merged.get("image_url"):
        for it in group:
            if it.get("image_url"):
                merged["image_url"] = it["image_url"]
                break
    if not merged.get("price") or merged.get("price") == "가격공개예정":
        for it in group:
            if it.get("price") and it["price"] != "가격공개예정":
                merged["price"] = it["price"]
                break
    return merged


def _deduplicate_gonggu(items: list[dict]) -> list[dict]:
    clusters: list[list[dict]] = []
    for item in items:
        for cluster in clusters:
            if any(_is_same_product(item, member) for member in cluster):
                cluster.append(item)
                break
        else:
            clusters.append([item])
    return [_merge_duplicate_group(c) if len(c) > 1 else c[0] for c in clusters]


def _build_summary_rows(start: date, end: date, hide_before: date | None = None) -> list[dict]:
    """검색/텍스트 보조 파일(calendar_summary.txt, view.html) 둘이 같이 쓰는
    [start, end] 기간 전체(카테고리 무관) 공구 목록. 중복 상품 통합 후 날짜순 정렬.

    hide_before를 주면 그보다 먼저 끝난(마감된) 공구는 결과에서 제외한다 -
    상시 최신/예정 데이터 중심 뷰를 유지하기 위함(과거에 이미 끝난 공구를
    당월 범위라는 이유만으로 계속 보여주지 않음)."""
    raw_items = gonggu_db.list_gonggu(start_date=start.isoformat(), end_date=end.isoformat())
    items = _deduplicate_gonggu(raw_items)

    if hide_before is not None:
        cutoff = hide_before.isoformat()
        items = [gb for gb in items if (gb.get("end_date") or gb.get("start_date") or "") >= cutoff]

    items.sort(key=lambda gb: gb.get("start_date") or "9999-99-99")

    multilink_map = _load_multilink_map()

    rows = []
    for idx, gb in enumerate(items):
        start = gb.get("start_date") or ""
        end = gb.get("end_date") or ""
        date_label = f"{start}~{end}" if end and end != start else start

        brand = gb.get("brand") or ""
        product_name = gb.get("product_name") or ""
        brand_product = f"{brand} {product_name}".strip() if brand else product_name
        price = gb.get("price") or "가격공개예정"
        image_url = gb.get("image_url") or ""

        influencer_display, influencer_handle = _parse_influencer(gb["influencer_name"])
        # instagram_id는 gonggu.db에 별도 컬럼이 없고 influencer_name에서 파싱한
        # 핸들이 유일한 단서이므로, 핸들이 있으면(=거의 항상) 무조건 프로필 링크를 건다.
        profile_url = f"https://www.instagram.com/{influencer_handle}/" if influencer_handle else ""
        influencer_label = _influencer_label(influencer_display, influencer_handle)

        raw_link = gb.get("purchase_link") or ""
        if raw_link.startswith("http"):
            purchase_url, purchase_label = raw_link, "구매하기 →"
        elif influencer_handle and multilink_map.get(influencer_handle, "").startswith("http"):
            purchase_url, purchase_label = multilink_map[influencer_handle], "멀티링크 확인 →"
        elif profile_url:
            purchase_url, purchase_label = profile_url, "프로필 방문 →"
        else:
            purchase_url, purchase_label = "", ""

        rows.append(
            {
                "row_id": f"row-{idx}",
                "date_label": date_label,
                "start_date": start,
                "end_date": end or start,
                "category": gb["category"],
                "color": CATEGORIES.get(gb["category"], {}).get("color", "#999"),
                "product_name": product_name,
                "brand": brand,
                "brand_product": brand_product,
                "price": price,
                "image_url": image_url,
                "influencer_name": gb["influencer_name"],
                "influencer_display": influencer_display,
                "influencer_handle": influencer_handle,
                "influencer_label": influencer_label,
                "influencer_profile_url": profile_url,
                "key_benefit": gb.get("key_benefit") or "",
                "purchase_url": purchase_url,
                "purchase_label": purchase_label,
            }
        )
    return rows


def _build_calendar_days(rows: list[dict], start: date, end: date) -> list[dict]:
    """검색용 HTML 뷰어 상단 캘린더 그리드/날짜 탭용 날짜별 버킷 (디둡된 rows 재사용).
    [start, end] 기간의 모든 날짜를 돈다 - 당월+익월 롤링 범위라 7일 고정이 아니라
    최대 두 달치(약 60일)까지 나올 수 있으며, 화면에서는 가로 스크롤로 넘겨 본다."""
    days = []
    d = start
    while d <= end:
        d_iso = d.isoformat()
        day_rows = [r for r in rows if r["start_date"] <= d_iso <= (r["end_date"] or r["start_date"])]
        days.append({"weekday_kr": WEEKDAY_KR[d.weekday()], "date_num": d.day, "date_iso": d_iso, "entries": day_rows})
        d += timedelta(days=1)
    return days


def _write_calendar_summary_txt(rows: list[dict], start: date, end: date, title: str | None) -> Path:
    """인스타 캡션으로 바로 붙여넣을 수 있는 텍스트 목록.
    한 줄 형식: [날짜 | 카테고리 | 브랜드/상품명 | 인플루언서]"""
    header_title = title or "육아/식품 공구 캘린더"
    lines = [f"📅 {header_title} ({start.strftime('%m.%d')} ~ {end.strftime('%m.%d')})", ""]

    if not rows:
        lines.append("해당 기간에 예정된 공구가 없습니다.")
    else:
        for r in rows:
            lines.append(f"[{r['date_label']} | {r['category']} | {r['brand_product']} | {r['influencer_name']}]")

    lines.append("")
    lines.append("#육아공구 #영유아식품 #키즈가구 #공구 #육아템")

    out_path = OUTPUT_DIR / "calendar_summary.txt"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path


def _write_view_html(rows: list[dict], start: date, end: date, title: str | None) -> Path:
    """'Buyg' 브랜드의 위시버니 스타일 모바일 커머스 뷰어 (서버 없이 더블클릭으로
    바로 열림). 마감임박/이달의공구/카테고리칩/이번주/D-14진행예정 6개 섹션은
    전부 JS가 rows_json 하나를 가지고 클라이언트에서 계산/렌더링한다 - 날짜 계산
    (마감임박, D-day 배지, 요일 바)이 '오늘' 기준으로 계속 바뀌어야 하는데,
    이 파일은 생성 시점에 한 번 굳는 정적 HTML이므로 JS 쪽에 today를 넘겨
    렌더링 시점이 아니라 "보는 시점의 오늘"로 다시 계산하게 하기 위함이다."""
    rows_json = json.dumps(rows, ensure_ascii=False).replace("</", "<\\/")
    html = render_card_html(
        {
            "title": title or "영유아 공구 캘린더",
            "range_text": f"{start.strftime('%Y.%m.%d')} ~ {end.strftime('%m.%d')}",
            "rows": rows,
            "rows_json": rows_json,
        },
        "view_page.html",
    )
    out_path = OUTPUT_DIR / "view.html"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")

    # GitHub Pages는 저장소 루트의 index.html을 진입점으로 서빙하므로 동일한
    # 내용을 프로젝트 루트에도 복사해둔다 (scripts/deploy_to_github.py가 이 파일을 커밋/푸시함).
    (BASE_DIR / "index.html").write_text(html, encoding="utf-8")

    return out_path


def run(today: date | None = None, week_start: date | None = None, title: str | None = None) -> list[str]:
    """today: '이번 주'/노출 기간 판단 기준일 (week_start가 없을 때만 사용).
    week_start: 명시적으로 특정 주(월요일)를 지정하고 싶을 때 사용 - 지정하면 today는
    무시되고, 카드뉴스뿐 아니라 view.html/calendar_summary.txt 범위도 그 주로 고정된다
    (특정 과거/미래 주를 다시 뽑아보고 싶을 때 쓰는 수동 오버라이드).
    title: 표지 카드의 메인 타이틀 문구 (없으면 기본 문구 사용)."""
    gonggu_db.init_db()
    today_date = today or date.today()

    if week_start is not None:
        monday = week_start
        sunday = monday + timedelta(days=6)
        folder_tag = f"week_{monday.strftime('%Y%m%d')}"
        display_start, display_end = monday, sunday
    else:
        # 카드뉴스(인스타 피드용 PNG 4장)는 계속 '이번 주' 기준을 쓰고,
        # view.html/calendar_summary.txt만 당월+익월 D-14 롤링 범위를 쓴다.
        monday, sunday = _current_week_range(today_date)
        folder_tag = datetime.now().strftime("%Y%m%d")
        display_start, display_end = _current_display_range(today_date)

    out_dir = OUTPUT_DIR / f"cardnews_{folder_tag}"
    out_dir.mkdir(parents=True, exist_ok=True)

    paths: list[str] = []

    cover_path = out_dir / "1_cover.png"
    render_card_png(_build_cover_context(monday, sunday, title), cover_path, "cover_card.html")
    paths.append(str(cover_path))
    logger.info("1장(표지) 생성: %s", cover_path)

    for idx, category in enumerate(CATEGORY_NAMES, start=2):
        context = _build_category_context(category, monday, sunday, page_info=f"{idx}/{len(CATEGORY_NAMES) + 1}")
        page_path = out_dir / f"{idx}_{category}.png"
        render_card_png(context, page_path, "category_card.html")
        paths.append(str(page_path))
        logger.info("%d장(%s) 생성: %s", idx, category, page_path)

    logger.info("카드뉴스 %d장 생성 완료: %s", len(paths), out_dir)

    summary_rows = _build_summary_rows(display_start, display_end, hide_before=today_date)
    summary_path = _write_calendar_summary_txt(summary_rows, display_start, display_end, title)
    logger.info("텍스트 캡션 요약 생성 (%s~%s, %d건): %s", display_start, display_end, len(summary_rows), summary_path)
    view_path = _write_view_html(summary_rows, display_start, display_end, title)
    logger.info("검색용 HTML 뷰어 생성: %s", view_path)

    return paths


if __name__ == "__main__":
    import sys

    arg_week_start = None
    arg_title = None
    if len(sys.argv) > 1:
        arg_week_start = datetime.strptime(sys.argv[1], "%Y-%m-%d").date()
    if len(sys.argv) > 2:
        arg_title = sys.argv[2]

    result_paths = run(week_start=arg_week_start, title=arg_title)
    print("\n".join(result_paths))
