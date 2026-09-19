"""[카카오 오픈채팅방 공지 텍스트 자동 생성]

빌드된 사이트(index.html)에 실려 있는 공구 목록에서 "마감 임박 BEST 3"를 뽑아,
오픈채팅방(알림방)에 그대로 복사해 붙일 수 있는 카톡 최적화 텍스트를
notices/kakao/YYYY-MM-DD.txt 로 저장한다. 자동 전송은 하지 않는다.

- 데이터는 DB가 아니라 방금 빌드된 index.html의 ROWS를 그대로 읽는다. 공지에 넣는
  #deal= 딥링크 키가 실제로 배포될 사이트 데이터에 존재해야 링크가 열리기 때문이다.
- 파일명 날짜 = "이 공지를 보내는 날". 파이프라인은 보통 오후에 끝나고 공지는 다음 날
  아침에 보내므로, MORNING_SEND_CUTOFF_HOUR 이후에 실행되면 내일 날짜로 만들어
  "오늘 밤 마감" 문구가 발송 시점에 사실이 되게 한다.
- run()은 어떤 오류가 나도 예외를 밖으로 던지지 않는다(파이프라인을 멈추지 않기 위함).

실행:
    python -m generator.kakao_notifier                # 기본 규칙(오후 실행이면 내일자)
    python -m generator.kakao_notifier 2026-09-20     # 발송일 직접 지정
"""
from __future__ import annotations

import json
import logging
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote

from config import BASE_DIR

logger = logging.getLogger(__name__)

# GitHub Pages 프로젝트 사이트라 /09-calendar/ 경로가 필요하다 (루트 도메인은 404).
SITE_URL = "https://buyg-kids.github.io/09-calendar/"
INDEX_HTML = BASE_DIR / "index.html"
NOTICE_DIR = BASE_DIR / "notices" / "kakao"

TOP_N = 3
MORNING_SEND_CUTOFF_HOUR = 12
MAX_NAME_LEN = 36
MAX_BENEFIT_LEN = 34

_ROWS_RE = re.compile(r"const ROWS = (\[.*?\]);\s*\n")
_AMOUNT_RE = re.compile(r"[\d,]+\s*(?:만|천)?\s*원")
_NAME_SUFFIX_RE = re.compile(r"\s*외\s*\d+종$")
_KEYCAPS = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]


def default_target_date(now: datetime | None = None) -> date:
    now = now or datetime.now()
    return now.date() + timedelta(days=1) if now.hour >= MORNING_SEND_CUTOFF_HOUR else now.date()


def _load_rows() -> list[dict]:
    html = INDEX_HTML.read_text(encoding="utf-8")
    m = _ROWS_RE.search(html)
    if not m:
        raise ValueError(f"{INDEX_HTML} 에서 ROWS 데이터를 찾지 못했습니다")
    return json.loads(m.group(1))


def _deal_key(row: dict) -> str:
    """view_page.html 의 favKey()와 반드시 같은 규칙이어야 딥링크가 그 카드를 찾는다."""
    name = re.sub(r"\s+", "", _NAME_SUFFIX_RE.sub("", row.get("product_name") or ""))
    return (row.get("influencer_handle") or row.get("influencer_name") or "") + "|" + (row.get("brand") or name)


def _deal_url(row: dict) -> str:
    return SITE_URL + "#deal=" + quote(_deal_key(row), safe="")


def _short_name(name: str) -> str:
    """카톡 한 줄에 들어오도록 줄바꿈/옵션 나열을 정리하고 길면 줄인다("외 N종"은 유지)."""
    s = re.sub(r"\s+", " ", (name or "").strip())
    suffix = ""
    m = _NAME_SUFFIX_RE.search(s)
    if m:
        suffix = " " + m.group(0).strip()
        s = s[:m.start()].strip()
    if len(s) + len(suffix) > MAX_NAME_LEN:
        no_paren = re.sub(r"\s*\([^)]*\)", "", s).strip()
        if no_paren:
            s = no_paren
    limit = MAX_NAME_LEN - len(suffix)
    if len(s) > limit:
        s = s[:limit - 1].rstrip() + "…"
    return s + suffix


def _simplify_price(raw: str) -> str:
    """view_page.html simplifyPrice() 포팅: '/'로 나열된 다품목 가격은 최저가 하나로 요약."""
    if "/" not in raw:
        return raw
    amounts = []
    for seg in raw.split("/"):
        idx = seg.rfind("→")
        relevant = seg[idx + 1:] if idx != -1 else seg
        found = re.findall(r"[\d,]+(?=\s*원)", relevant)
        if found:
            try:
                amounts.append(int(found[-1].replace(",", "")))
            except ValueError:
                pass
    if len(amounts) < 2:
        return raw
    return f"최저 {min(amounts):,}원 (외 {len(amounts) - 1}종)"


def _price_text(row: dict) -> str | None:
    """실제 가격/할인 문구가 있으면 그 텍스트, 가격이 아닌 값(미표기, 숫자 없음, 배송비 등)이면 None."""
    raw = _simplify_price((row.get("price") or "").strip())
    if not raw or raw == "가격공개예정" or not re.search(r"\d", raw) or raw.startswith("배송비"):
        return None

    if "→" in raw:
        before, after = raw.rsplit("→", 1)
        final = re.sub(r"^\s*공구가\s*", "", after).strip()
        original = re.sub(r"^\s*(정가|정상가)\s*", "", before).strip()
        if re.search(r"\d", final):
            return final + (f" (정가 {original})" if re.search(r"\d", original) else "")
        return None

    raw = re.sub(r"^\s*공구가\s*", "", raw).strip()
    # 구매조건이 섞인 긴 설명문은 대표 금액 하나만 (view_page.html isDescriptivePriceText와 같은 기준)
    if len(_AMOUNT_RE.findall(raw)) >= 2 or len(raw) > 20:
        first = _AMOUNT_RE.search(raw)
        return re.sub(r"\s+", "", first.group(0)) + "~" if first else None
    return raw


def _price_line(row: dict) -> str:
    price = _price_text(row)
    if price:
        return f"- 공구가: {price}"
    benefit = re.sub(r"\s+", " ", (row.get("key_benefit") or "").strip())
    if benefit:
        if len(benefit) > MAX_BENEFIT_LEN:
            benefit = benefit[:MAX_BENEFIT_LEN - 1].rstrip() + "…"
        return f"- 혜택: {benefit}"
    return "- 공구가: 링크에서 확인"


def _quality_score(row: dict) -> int:
    """같은 마감일끼리 어떤 걸 먼저 보여줄지: 실제 가격 > 혜택 문구 > 게시물/구매 링크 보유."""
    score = 0
    if _price_text(row):
        score += 3
    if (row.get("key_benefit") or "").strip():
        score += 1
    if row.get("purchase_label") != "프로필 방문 →" and (row.get("purchase_url") or "").startswith("http"):
        score += 1
    return score


def _select(rows: list[dict], target: date) -> list[tuple[dict, int]]:
    """발송일에 진행 중인 공구를 마감이 가까운 순 -> 정보가 충실한 순으로 정렬해 TOP_N개.
    같은 인플루언서가 한 공지를 독점하지 않도록 서로 다른 인플루언서를 먼저 채운다."""
    t = target.isoformat()
    ranked = []
    for r in rows:
        start = r.get("start_date") or ""
        end = r.get("end_date") or start
        if not end or end < t or (start and start > t):
            continue
        try:
            days_left = (date.fromisoformat(end) - target).days
        except ValueError:
            continue
        if not _short_name(r.get("product_name") or ""):
            continue
        ranked.append((days_left, -_quality_score(r), r.get("product_name") or "", r))
    ranked.sort(key=lambda x: (x[0], x[1], x[2]))

    picked: list[tuple[dict, int]] = []
    seen_influencers: set[str] = set()
    for days_left, _, _, r in ranked:
        who = r.get("influencer_handle") or r.get("influencer_name") or ""
        if who in seen_influencers:
            continue
        picked.append((r, days_left))
        seen_influencers.add(who)
        if len(picked) == TOP_N:
            return picked
    for days_left, _, _, r in ranked:  # 인플루언서가 부족하면 중복 허용으로 채운다
        if len(picked) == TOP_N:
            break
        if not any(r is p for p, _ in picked):
            picked.append((r, days_left))
    return picked


def _deadline_tag(days_left: int, row: dict, target: date) -> str:
    if days_left <= 0:
        return ""
    if days_left == 1:
        return " (내일 마감)"
    end = date.fromisoformat(row.get("end_date") or row["start_date"])
    return f" ({end.month}/{end.day} 마감)"


def _build_text(picks: list[tuple[dict, int]], target: date) -> str:
    all_tonight = all(d <= 0 for _, d in picks)
    head = "오늘 밤 마감되는" if all_tonight else "곧 마감되는"
    lines = [f"🔔 [Buyg] {head} 육아 공구 BEST {len(picks)}", ""]
    for i, (row, days_left) in enumerate(picks):
        lines.append(f"{_KEYCAPS[i]} {_short_name(row.get('product_name') or '')}{_deadline_tag(days_left, row, target)}")
        lines.append(_price_line(row))
        lines.append(f"- 링크: {_deal_url(row)}")
        lines.append("")
    lines.append("👉 실시간 전체 육아 공구 달력 보기:")
    lines.append(SITE_URL)
    return "\n".join(lines) + "\n"


def run(target_date: date | None = None) -> Path | None:
    """공지 텍스트 파일을 만들고 경로를 반환한다. 실패/대상 없음이면 None (예외는 던지지 않음)."""
    try:
        target = target_date or default_target_date()
        rows = _load_rows()
        picks = _select(rows, target)
        if not picks:
            logger.warning("카카오 공지: %s에 진행 중인 공구가 없어 텍스트를 만들지 않았습니다", target)
            return None
        NOTICE_DIR.mkdir(parents=True, exist_ok=True)
        path = NOTICE_DIR / f"{target.isoformat()}.txt"
        path.write_text(_build_text(picks, target), encoding="utf-8")
        logger.info("카카오 공지 텍스트 생성 (발송일 %s, %d건): %s", target, len(picks), path)
        return path
    except Exception as e:
        # logger.exception 금지: run_nightly_pipeline.ps1이 로그의 "Traceback"을 파이프라인 실패로
        # 판정해 배포를 건너뛴다. 이 공지는 부가 산출물이라 실패해도 사이트 배포를 막으면 안 된다.
        logger.error("카카오 공지 텍스트 생성 실패 - 무시하고 계속 진행 (%s: %s)", type(e).__name__, str(e)[:200])
        return None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    arg = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else None
    out = run(arg)
    if out:
        print(out.read_text(encoding="utf-8"))
