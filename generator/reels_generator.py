"""[Agent 5: 인스타 릴스 대본/캡션 자동 생성기]

야간 파이프라인 Step 3에서 view.html용 summary rows가 만들어진 직후(card_news.run 끝부분)
호출되어, 당일 마감 임박 공구 3개로 릴스(15~20초) 대본 + 캡션을 만들어
    reels/YYYY-MM-DD_reels.md
로 저장한다 (프로젝트 루트, 폴더 없으면 자동 생성, 같은 날 재실행 시 덮어씀).

포맷 (REELS_FORMAT 환경변수 또는 run(fmt=...)로 선택, 기본은 deadline_top3):
  deadline_top3 - 마감 임박 육아템 TOP 3
  growth_stage  - 성장 단계별 필수템 모음
  price_compare - 공구가 vs 최저가 실속 비교

파이프라인 부작용 방지: run()은 어떤 상황에서도 예외를 밖으로 던지지 않고
(API 키 없음/데이터 없음/LLM 오류/저장 오류 -> 로그만 남기고 None 반환),
실패 시 기존 파일을 건드리지 않는다.

실행 (단독 테스트):
    python -m generator.reels_generator
"""
from __future__ import annotations

import logging
import os
import re
from datetime import date, datetime
from pathlib import Path

from config import ANTHROPIC_API_KEY, BASE_DIR, CLAUDE_MODEL

logger = logging.getLogger(__name__)

REELS_DIR = BASE_DIR / "reels"
DEFAULT_FORMAT = "deadline_top3"
TOP_N = 3
API_TIMEOUT_SEC = 60

CTA_TEXT = "댓글로 '달력' 남겨주시면 실시간 공구 달력 링크를 DM으로 바로 보내드려요!"
BASE_TAGS = ["#육아공구", "#핫딜", "#Buyg"]

FORMATS = {
    "deadline_top3": {
        "title": "마감 임박 육아템 TOP 3",
        "guide": "가장 빨리 끝나는 공구 3개를 D-day 순으로 카운트다운하듯 소개한다. "
                 "'놓치면 다음 공구까지 기다려야 해요' 식의 마감 긴박감이 핵심.",
    },
    "growth_stage": {
        "title": "성장 단계별 필수템 모음",
        "guide": "3개 상품을 아이 성장 단계(예: 신생아~백일 / 뒤집기·이유식 / 걸음마·놀이)에 "
                 "억지스럽지 않게 연결해 '지금 시기에 필요한 템' 관점으로 소개한다. "
                 "상품 데이터에 없는 개월수 스펙은 단정하지 말고 일반적인 표현만 쓴다.",
    },
    "price_compare": {
        "title": "공구가 vs 최저가 실속 비교",
        "guide": "제공된 공구 가격/혜택을 근거로 '공구로 사면 뭐가 이득인지'를 설명한다. "
                 "최저가 실제 금액은 데이터에 없으므로 절대 지어내지 말고, "
                 "'검색해서 직접 비교해 보세요' 식으로 안내한다.",
    },
}

SYSTEM_PROMPT = """너는 육아 공구 큐레이션 서비스 'Buyg (Buy Grow)'의 인스타 릴스 카피라이터다.
서비스 정의: "아이 성장에 맞춰 꼭 필요한 물건들을 가장 합리적으로 구매하는 육아 공구 달력".

[톤앤매너]
- 친한 조리원 동기(조동) 언니가 꿀팁을 알려주는 똑 부러지는 말투 (반말 아닌 친근한 해요체).
- 0~3초 안에 시선을 붙잡는 훅, 전체 15~20초의 빠른 호흡 (나레이션 총 분량 약 80~110자 내외의 씬 3~5개).
- 과장·허위 금지: 아래 [상품 데이터]에 없는 가격, 할인율, 스펙, 후기, 최저가 수치는 절대 만들지 않는다.
- 가격은 데이터에 적힌 그대로만 언급하고, 없으면 가격은 언급하지 않는다.
- 마감 시각(몇 시까지 등)은 데이터에 없으므로 '오늘 마감', 'D-3'처럼 D-day 표현만 쓴다 ('자정', '밤 12시', '오늘 밤' 같은 시각 표현 금지). 배송/소재/기능 등 데이터에 없는 스펙도 쓰지 않는다.
- 나레이션 순서는 [상품 데이터] 번호 순서(1→3)를 따르고, 씬 하나에 상품 하나씩 소개한다. 상품명은 짧고 자연스럽게 줄여 말한다.
- 마지막 씬은 댓글 유도(CTA)로 자연스럽게 이어지게 쓴다 (CTA 문구 자체는 시스템이 따로 붙인다).

반드시 build_reels 도구로만 답한다."""

REELS_TOOL = {
    "name": "build_reels",
    "description": "릴스 훅, 씬별 대본, 캡션과 해시태그를 작성한다.",
    "input_schema": {
        "type": "object",
        "properties": {
            "hook_text": {"type": "string", "description": "0~3초 화면에 띄울 시선 강탈 텍스트 (한 줄)"},
            "hook_visual": {"type": "string", "description": "0~3초 화면 연출 가이드"},
            "scenes": {
                "type": "array",
                "description": "씬별 대본 3~5개",
                "items": {
                    "type": "object",
                    "properties": {
                        "time": {"type": "string", "description": "예: 3-8초"},
                        "narration": {"type": "string", "description": "CapCut/TTS에 그대로 넣을 나레이션"},
                        "visual": {"type": "string", "description": "화면 비주얼 지시문"},
                    },
                    "required": ["time", "narration", "visual"],
                },
            },
            "caption": {"type": "string", "description": "인스타그램 본문 캡션 (이모지 적당히, 3~6줄)"},
            "hashtags": {"type": "array", "items": {"type": "string"}, "description": "타깃 해시태그 8~12개, # 포함"},
        },
        "required": ["hook_text", "hook_visual", "scenes", "caption", "hashtags"],
    },
}


def _parse_date(s: str | None) -> date | None:
    try:
        return datetime.strptime((s or "")[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


_PRICE_PLACEHOLDERS = ("가격공개예정", "최저가", "미정")


def _has_price(r: dict) -> bool:
    price = r.get("price") or ""
    return any(ch.isdigit() for ch in price) and not any(w in price for w in _PRICE_PLACEHOLDERS)


def _tokens(r: dict) -> set[str]:
    text = f"{r.get('brand', '')} {r.get('product_name', '')}"
    return {t for t in re.findall(r"[0-9A-Za-z가-힣]{3,}", text) if not re.fullmatch(r"\d+[가-힣]*차?", t) and t not in ("공구", "공동구매")}


def pick_products(rows: list[dict], today: date, fmt: str = DEFAULT_FORMAT, n: int = TOP_N) -> list[dict]:
    """마감 임박(아직 안 끝난) 순으로 n개. 같은 브랜드/상품 중복은 건너뛰고,
    growth_stage는 카테고리가 겹치지 않게 먼저 채운 뒤 부족하면 나머지로 채운다."""
    live = []
    for r in rows:
        end = _parse_date(r.get("end_date"))
        start = _parse_date(r.get("start_date"))
        if not end or end < today or not (r.get("product_name") or "").strip():
            continue
        if fmt == "price_compare" and not _has_price(r):
            continue
        # 시작 전(예정) 공구는 임박 순위에서 뒤로 - 진행 중인 것 우선
        upcoming = 1 if (start and start > today) else 0
        live.append((upcoming, end, 0 if _has_price(r) else 1, r))
    live.sort(key=lambda t: t[:3])

    picked: list[dict] = []
    seen_keys: set[str] = set()

    def _try_add(r: dict, distinct_category: bool) -> None:
        if len(picked) >= n:
            return
        # 같은 상품이 브랜드/표기만 달리 여러 행으로 남는 경우(예: '아리매트 2차' / '도담도담 아리매트')를
        # 2글자 이상 한글/영숫자 토큰 겹침으로 걸러낸다.
        tokens = _tokens(r)
        if tokens & seen_keys:
            return
        if distinct_category and any(p["category"] == r.get("category") for p in picked):
            return
        seen_keys.update(tokens)
        picked.append(r)

    if fmt == "growth_stage":
        for *_, r in live:
            _try_add(r, True)
    for *_, r in live:
        _try_add(r, False)
    return picked


def _describe_products(products: list[dict], today: date) -> str:
    lines = []
    for i, p in enumerate(products, start=1):
        end = _parse_date(p.get("end_date"))
        dday = (end - today).days if end else None
        dday_label = "오늘 마감" if dday == 0 else f"D-{dday}" if dday is not None else "마감일 미정"
        lines.append(
            f"{i}. {p.get('brand_product') or p.get('product_name')}\n"
            f"   - 카테고리: {p.get('category', '')}\n"
            f"   - 공구 기간: {p.get('date_label', '')} ({dday_label})\n"
            f"   - 공구가: {p.get('price') if _has_price(p) else '미공개 (대본/캡션에서 가격 언급 금지)'}\n"
            f"   - 혜택: {p.get('key_benefit') or '없음'}"
        )
    return "\n".join(lines)


def _call_llm(products: list[dict], fmt: str, today: date) -> dict:
    import anthropic

    spec = FORMATS[fmt]
    user_msg = (
        f"오늘 날짜: {today.isoformat()}\n"
        f"릴스 포맷: {spec['title']}\n"
        f"포맷 가이드: {spec['guide']}\n\n"
        f"[상품 데이터]\n{_describe_products(products, today)}"
    )
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY, timeout=API_TIMEOUT_SEC, max_retries=1)
    resp = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=1500,
        system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
        tools=[REELS_TOOL],
        tool_choice={"type": "tool", "name": "build_reels"},
        messages=[{"role": "user", "content": user_msg}],
    )
    for block in resp.content:
        if block.type == "tool_use" and block.name == "build_reels":
            return block.input
    raise ValueError("LLM 응답에 build_reels 결과가 없음")


def _render_markdown(data: dict, products: list[dict], fmt: str, today: date) -> str:
    spec = FORMATS[fmt]
    scenes = data.get("scenes") or []
    if not (data.get("hook_text") and scenes and data.get("caption")):
        raise ValueError("LLM 결과에 훅/씬/캡션이 비어 있음")

    # 기본 태그를 항상 포함하고, LLM 태그는 중복 제거 후 뒤에 붙인다.
    tags: list[str] = []
    for t in BASE_TAGS + list(data.get("hashtags") or []):
        t = str(t).strip()
        if t and not t.startswith("#"):
            t = "#" + t
        if t and t not in tags:
            tags.append(t)

    out = [
        f"# Buyg 릴스 - {today.isoformat()} ({spec['title']})",
        "",
        f"> 생성 모델: {CLAUDE_MODEL} · 자동 생성 초안이니 업로드 전 가격/마감일을 view.html과 대조하세요.",
        "",
        "## 오늘의 소재",
        "",
    ]
    for i, p in enumerate(products, start=1):
        out.append(
            f"{i}. **{p.get('brand_product') or p.get('product_name')}** — {p.get('price', '')} "
            f"· {p.get('date_label', '')} · {p.get('influencer_label') or p.get('influencer_name', '')}"
        )
    out += [
        "",
        "## [Hook] 0~3초",
        "",
        f"- **화면 텍스트**: {data['hook_text']}",
        f"- **연출 가이드**: {data.get('hook_visual', '')}",
        "",
        "## [Script]",
        "",
    ]
    for i, sc in enumerate(scenes, start=1):
        out += [
            f"**Scene {i} ({sc.get('time', '')})**",
            f"- 나레이션: {sc.get('narration', '')}",
            f"- 비주얼: {sc.get('visual', '')}",
            "",
        ]
    out += [
        "## [CTA]",
        "",
        CTA_TEXT,
        "",
        "## [Caption & Tags]",
        "",
        data["caption"].strip(),
        "",
        " ".join(tags),
        "",
    ]
    return "\n".join(out)


def run(rows: list[dict] | None = None, today: date | None = None, fmt: str | None = None) -> Path | None:
    """rows: card_news._build_summary_rows() 결과. None이면 DB에서 직접 조회.
    성공 시 저장된 파일 경로, 스킵/실패 시 None. 예외는 절대 밖으로 던지지 않는다."""
    try:
        today = today or date.today()
        fmt = fmt or os.getenv("REELS_FORMAT") or DEFAULT_FORMAT
        if fmt not in FORMATS:
            logger.warning("릴스: 알 수 없는 포맷 '%s' -> 기본(%s) 사용", fmt, DEFAULT_FORMAT)
            fmt = DEFAULT_FORMAT

        if not ANTHROPIC_API_KEY:
            logger.warning("릴스 생성 스킵: ANTHROPIC_API_KEY 없음")
            return None

        if rows is None:
            from generator.card_news import _build_summary_rows, _current_display_range

            start, end = _current_display_range(today)
            rows = _build_summary_rows(start, end, hide_before=today)

        products = pick_products(rows, today, fmt)
        if not products:
            logger.warning("릴스 생성 스킵: 조건에 맞는 공구가 없음 (rows=%d)", len(rows))
            return None

        data = _call_llm(products, fmt, today)
        markdown = _render_markdown(data, products, fmt, today)

        REELS_DIR.mkdir(parents=True, exist_ok=True)
        path = REELS_DIR / f"{today.isoformat()}_reels.md"
        tmp = path.with_suffix(".md.tmp")
        tmp.write_text(markdown, encoding="utf-8")
        tmp.replace(path)  # 쓰다 죽어도 기존 파일이 반쪽으로 남지 않게 원자적으로 교체
        logger.info("릴스 대본/캡션 생성 완료 (%s, %d개 상품): %s", FORMATS[fmt]["title"], len(products), path)
        return path
    except Exception as e:
        # 주의: logger.exception 금지 - run_nightly_pipeline.ps1이 로그의 "Traceback"을 실패로 판정해
        # 배포/절전을 건너뛰므로, 부가 산출물인 릴스 실패가 배포를 막지 않게 한 줄로만 남긴다.
        logger.error("릴스 생성 실패 - 파이프라인은 계속 진행 (%s: %s)", type(e).__name__, str(e)[:200])
        return None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    result = run()
    print(result if result else "릴스 파일 생성 안 됨 (로그 확인)")
