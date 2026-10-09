"""[키워드 알림 매칭] 카카오톡 채널로 들어온 고객의 '나만의 키워드 알림 신청'을 야간 공구 목록과 대조한다.

흐름 (야간 파이프라인 generator/card_news.py 의 _match_alert_keywords 훅이 호출):
  data/alert_keywords.json (고객 키워드)  x  오늘의 공구 목록(summary rows + 원본 캡션)
    -> 상품명/브랜드/혜택 요약/본문(캡션)에 키워드가 들어 있으면 매칭
    -> 관리자가 카톡 1:1 채팅에 그대로 복사해 붙일 메시지를 만들어
       output/matched_alerts_today.json, output/matched_alerts_today.txt 에 저장하고 로그에 눈에 띄게 출력한다.
  같은 (고객 키워드, 공구)는 data/alert_state.json 으로 기억해 다음 날 중복 안내를 만들지 않는다(신규만 메시지 생성).

개인정보: 고객 이름/키워드는 data/ 와 output/ 에만 저장한다. 두 폴더는 .gitignore 대상이라 공개 저장소에 올라가지 않는다 -
          이 모듈은 그 밖의 경로(공개 산출물 index.html, *.json 등)에는 아무것도 쓰지 않는다.

안전 규칙: run()은 절대 예외를 던지지 않는다(야간 로그에 'Traceback' 이 남으면 배포가 건너뛰어진다) - 실패하면
          logger.error 한 줄만 남기고 None 을 돌려준다. logger.exception 사용 금지.

수동 실행:
    python -m generator.alert_matcher --init        # data/alert_keywords.json 샘플 생성(이미 있으면 그대로)
    python -m generator.alert_matcher               # 현재 DB 로 매칭 실행(상태 기록 포함)
    python -m generator.alert_matcher --no-state    # 미리보기: 중복 방지 상태를 기록하지 않음
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import unicodedata
from datetime import date, datetime
from pathlib import Path
from typing import Protocol

from config import BASE_DIR, OUTPUT_DIR

logger = logging.getLogger(__name__)

KEYWORDS_PATH = BASE_DIR / "data" / "alert_keywords.json"
STATE_PATH = BASE_DIR / "data" / "alert_state.json"
OUTPUT_JSON = OUTPUT_DIR / "matched_alerts_today.json"     # 기존 산출물 폴더(output/, gitignore) - 고객 개인정보가 공개되지 않도록
OUTPUT_TXT = OUTPUT_DIR / "matched_alerts_today.txt"
SITE_URL = "https://buyg.kr/"
MIN_KEYWORD_LEN = 2          # 한 글자 키워드는 거의 모든 공구에 걸리므로 무시한다(정규화 후 길이 기준)

SAMPLE_KEYWORDS = [
    {"id": 1, "keyword": "트립트랩", "customer_name": "테스트고객", "created_at": "2026-10-09", "status": "active"},
]

MESSAGE_TEMPLATE = (
    "[BUYG 공구 알림]\n"
    "안녕하세요, {customer_name}님!\n"
    "요청하신 '{keyword}' 공구 일정이 오픈되었습니다.\n"
    "- 상품: {deal_title}\n"
    "- 링크: {deal_url}\n"
    "즐거운 하루 되세요! 😊"
)


# ---------------------------------------------------------------------------
# 순수 함수 (테스트 대상)
# ---------------------------------------------------------------------------
def normalize(text: str) -> str:
    """공백/기호를 없애고 소문자로 맞춘다 - '트립 트랩', '트립-트랩' 도 '트립트랩' 키워드에 걸리게."""
    t = unicodedata.normalize("NFC", text or "").casefold()
    return re.sub(r"[\W_]+", "", t)


def build_message(customer_name: str, keyword: str, deal_title: str, deal_url: str) -> str:
    return MESSAGE_TEMPLATE.format(customer_name=customer_name, keyword=keyword, deal_title=deal_title, deal_url=deal_url)


def deal_key(row: dict) -> str:
    """같은 공구를 날마다 같은 키로 식별한다(인플루언서 + 상품명 + 시작일). 시작일이 바뀐 새 회차는 새 공구로 본다."""
    who = (row.get("influencer_handle") or row.get("influencer_name") or "").lower()
    return f"{who}|{normalize(row.get('product_name') or '')}|{row.get('start_date') or ''}"


def _deal_title(row: dict) -> str:
    return (row.get("brand_product") or row.get("product_name") or "").strip()


def _deal_url(row: dict) -> str:
    return (row.get("purchase_url") or row.get("post_url") or SITE_URL).strip()


def load_keywords(path: Path = KEYWORDS_PATH) -> list[dict]:
    """활성(active) 키워드만. 파일이 없거나 형식이 깨졌으면 [] (파이프라인을 멈추지 않는다)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        logger.info("[키워드 알림] %s 없음 - 매칭 건너뜀 (python -m generator.alert_matcher --init 로 생성)", path.name)
        return []
    except (OSError, ValueError) as e:
        logger.error("[키워드 알림] %s 읽기 실패 - 매칭 건너뜀 (%s)", path.name, type(e).__name__)
        return []
    out = []
    for e in data if isinstance(data, list) else []:
        if not isinstance(e, dict) or (e.get("status") or "active") != "active":
            continue
        kw = str(e.get("keyword") or "").strip()
        if len(normalize(kw)) < MIN_KEYWORD_LEN:
            continue
        out.append({"id": e.get("id"), "keyword": kw, "norm": normalize(kw),
                    "customer_name": str(e.get("customer_name") or "고객").strip() or "고객"})
    return out


def match_deals(rows: list[dict], keywords: list[dict], captions: dict | None = None, today: str = "") -> list[dict]:
    """(고객 키워드 x 공구) 매칭. captions: {(influencer_name, product_name): 원본 캡션} - 있으면 본문까지 검사한다.
    마감된 공구(end_date < today)는 건너뛴다. 한 (키워드, 공구) 쌍은 한 번만, 처음 걸린 항목을 matched_in 으로 기록."""
    captions = captions or {}
    matches, seen = [], set()
    for row in rows:
        end = row.get("end_date") or row.get("start_date") or ""
        if today and end and end < today:
            continue
        fields = [
            ("상품명", normalize(f"{row.get('brand_product') or ''} {row.get('product_name') or ''}")),
            ("브랜드", normalize(row.get("brand") or "")),
            ("혜택 요약", normalize(row.get("key_benefit") or "")),
            ("본문", normalize(captions.get((row.get("influencer_name"), row.get("product_name"))) or "")),
        ]
        key = deal_key(row)
        for kw in keywords:
            hit = next((label for label, text in fields if text and kw["norm"] in text), None)
            pair = (kw["id"], kw["keyword"], key)
            if not hit or pair in seen:
                continue
            seen.add(pair)
            title, url = _deal_title(row), _deal_url(row)
            matches.append({
                "alert_id": kw["id"], "keyword": kw["keyword"], "customer_name": kw["customer_name"],
                "deal_title": title, "deal_url": url, "matched_in": hit,
                "influencer": row.get("influencer_label") or row.get("influencer_name") or "",
                "start_date": row.get("start_date") or "", "end_date": row.get("end_date") or "",
                "deal_key": key, "needs_review": hit == "본문", "post_key": row.get("post_url") or key,
                "message": build_message(kw["customer_name"], kw["keyword"], title, url),
            })
    return _collapse_body_matches(matches, keywords)


def _collapse_body_matches(matches: list[dict], keywords: list[dict]) -> list[dict]:
    """본문(캡션) 매칭은 '한 게시물에 여러 상품'이 묶여 있을 때 그 게시물의 모든 상품이 걸려 오탐이 많다.
    ① 같은 게시물에서 상품명/브랜드/혜택으로 이미 매칭됐으면 본문 매칭은 버리고 ② 아니면 게시물당 한 건으로 합쳐
    '… 외 N종 (게시물에서 키워드 언급)'으로 표시하며 needs_review=True 로 표시한다(관리자가 보내기 전에 확인)."""
    strong = {(m["alert_id"], m["keyword"], m["post_key"]) for m in matches if m["matched_in"] != "본문"}
    out, groups = [], {}
    for m in matches:
        if m["matched_in"] != "본문":
            out.append(m)
            continue
        gk = (m["alert_id"], m["keyword"], m["post_key"])
        if gk in strong:
            continue
        groups.setdefault(gk, []).append(m)
    by_id = {k["id"]: k for k in keywords}
    for gk, items in groups.items():
        first = dict(items[0])
        if len(items) > 1:
            first["deal_title"] = f"{first['deal_title']} 외 {len(items) - 1}종 (게시물에서 '{first['keyword']}' 언급)"
            first["message"] = build_message(first["customer_name"], first["keyword"], first["deal_title"], first["deal_url"])
        out.append(first)
    return out


# ---------------------------------------------------------------------------
# 상태(중복 방지) / 파일 입출력
# ---------------------------------------------------------------------------
def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write_json_atomic(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _state_key(m: dict) -> str:
    return f"{m['alert_id']}::{m['keyword']}::{m['deal_key']}"


def init_keywords_file(path: Path = KEYWORDS_PATH) -> bool:
    """샘플 한 건으로 키워드 파일을 만든다. 이미 있으면 건드리지 않는다(고객 데이터 보호). 새로 만들었으면 True."""
    if path.exists():
        return False
    _write_json_atomic(path, SAMPLE_KEYWORDS)
    return True


def _load_captions(today_iso: str) -> dict:
    """원본 캡션(본문 매칭용). DB 조회만 하고 실패하면 빈 dict - 본문 매칭만 빠진다."""
    try:
        import gonggu_db
        return {(g.get("influencer_name"), g.get("product_name")): g.get("caption_text") or ""
                for g in gonggu_db.list_gonggu(start_date=today_iso) if g.get("caption_text")}
    except Exception as e:  # noqa: BLE001
        logger.error("[키워드 알림] 원본 캡션 조회 실패 - 본문 매칭 없이 진행 (%s)", type(e).__name__)
        return {}


def _log_safe(text: str) -> str:
    """콘솔 인코딩(cp949 등)이 못 쓰는 문자(😊 등)를 ? 로 바꿔, 로깅이 UnicodeEncodeError 트레이스백을 남기지 않게 한다."""
    enc = getattr(sys.stderr, "encoding", None) or "utf-8"
    try:
        return text.encode(enc, errors="replace").decode(enc, errors="replace")
    except LookupError:
        return text.encode("ascii", errors="replace").decode("ascii")


def _log_block(result: dict) -> None:
    bar = "=" * 64
    logger.info(bar)
    logger.info(_log_safe(f"★ [키워드 알림] 오늘 매칭 {result['total_matches']}건 (신규 {result['new_count']}건, 이전 안내 {result['repeat_count']}건)"
                          f" / 활성 키워드 {result['active_keywords']}개"))
    if result["new_count"]:
        logger.info("★ 아래 메시지를 카톡 1:1 채팅에 복사해 보내세요 (전체: output/matched_alerts_today.txt)")
        for m in result["matches"]:
            if m["is_new"]:
                note = " - 본문 매칭이라 보내기 전 확인 필요" if m.get("needs_review") else ""
                logger.info(_log_safe(f"----- {m['customer_name']}님 / '{m['keyword']}' ({m['matched_in']}에서 매칭{note}) -----\n{m['message']}"))
    logger.info(bar)


# ---------------------------------------------------------------------------
# 알림 전송 인터페이스 (Skeleton) - 지금은 전송하지 않는다(ALERT_PUSH_PROVIDER 기본값 none)
# ---------------------------------------------------------------------------
class AlertNotifier(Protocol):
    """관리자에게 요약을 자동 푸시하는 전송 수단. 구현체는 send()가 예외를 던지지 않고 성공 여부(bool)만 돌려준다."""
    name: str

    def send(self, text: str) -> bool: ...


class NullNotifier:
    """기본값: 아무것도 보내지 않는다(콘솔 로그와 output/ 파일만 사용)."""
    name = "none"

    def send(self, text: str) -> bool:
        return False


class KakaoMemoNotifier:
    """카카오 '나에게 보내기'(generator/send_to_me.py 의 send_alert 재사용). ALERT_PUSH_PROVIDER=kakao_memo 일 때만 사용."""
    name = "kakao_memo"

    def send(self, text: str) -> bool:
        try:
            from generator.send_to_me import send_alert
            send_alert(text)
            return True
        except Exception as e:  # noqa: BLE001
            logger.error("[키워드 알림] 카카오 나에게 보내기 실패 (%s)", type(e).__name__)
            return False


class TelegramNotifier:
    """텔레그램 봇 웹훅 - 미구현 골격. 구현 시 .env 의 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 를 읽어
    https://api.telegram.org/bot<token>/sendMessage 로 POST 한다(토큰은 로그/예외 메시지에 남기지 않는다)."""
    name = "telegram"

    def send(self, text: str) -> bool:
        # TODO: requests.post(...) 구현. 지금은 설정만 확인하고 보내지 않는다.
        logger.warning("[키워드 알림] 텔레그램 전송은 아직 구현되지 않았습니다(골격).")
        return False


_NOTIFIERS = {"none": NullNotifier, "kakao_memo": KakaoMemoNotifier, "telegram": TelegramNotifier}


def get_notifier(provider: str | None = None) -> AlertNotifier:
    """provider 인자 또는 환경변수 ALERT_PUSH_PROVIDER(none | kakao_memo | telegram). 알 수 없으면 none."""
    name = (provider if provider is not None else os.getenv("ALERT_PUSH_PROVIDER", "none")).strip().lower()
    return _NOTIFIERS.get(name, NullNotifier)()


def push_admin_summary(result: dict, notifier: AlertNotifier | None = None) -> bool:
    """신규 매칭이 있을 때만 관리자에게 짧은 요약(개수·키워드)을 푸시한다. 고객 이름은 보내지 않는다. 실패해도 예외 없음."""
    try:
        if not result or not result.get("new_count"):
            return False
        notifier = notifier or get_notifier()
        kws = sorted({m["keyword"] for m in result["matches"] if m["is_new"]})
        text = f"[BUYG] 키워드 알림 신규 {result['new_count']}건 ({', '.join(kws[:5])}) - output/matched_alerts_today.txt 의 메시지를 카톡으로 보내 주세요."
        return bool(notifier.send(text))
    except Exception as e:  # noqa: BLE001
        logger.error("[키워드 알림] 관리자 푸시 단계 오류 - 무시 (%s)", type(e).__name__)
        return False


# ---------------------------------------------------------------------------
def run(rows: list[dict], today: date | None = None, *, update_state: bool = True,
        keywords_path: Path = KEYWORDS_PATH, state_path: Path = STATE_PATH,
        captions: dict | None = None, notifier: AlertNotifier | None = None) -> dict | None:
    """오늘의 공구 rows 와 고객 키워드를 매칭해 산출물/로그를 만든다. 절대 예외를 던지지 않는다."""
    try:
        today = today or date.today()
        keywords = load_keywords(keywords_path)
        if not keywords:
            return None
        if captions is None:
            captions = _load_captions(today.isoformat())
        matches = match_deals(rows, keywords, captions, today.isoformat())

        state = _read_json(state_path, {})
        notified = state.get("notified", {}) if isinstance(state, dict) else {}
        for m in matches:
            m["is_new"] = _state_key(m) not in notified
        new = [m for m in matches if m["is_new"]]

        result = {
            "generated_at": datetime.now().isoformat(timespec="seconds"), "date": today.isoformat(),
            "active_keywords": len(keywords), "total_matches": len(matches), "new_count": len(new),
            "repeat_count": len(matches) - len(new), "matches": matches,
        }
        _write_json_atomic(OUTPUT_JSON, result)
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        OUTPUT_TXT.write_text("\n\n".join(m["message"] for m in new) if new else "(오늘 새로 매칭된 키워드 알림이 없습니다)\n", encoding="utf-8")
        _log_block(result)

        if update_state and new:
            for m in new:
                notified[_state_key(m)] = today.isoformat()
            _write_json_atomic(state_path, {"notified": notified})
        push_admin_summary(result, notifier)
        return result
    except Exception as e:  # noqa: BLE001
        logger.error("[키워드 알림] 매칭 단계 오류 - 무시하고 계속 진행 (%s)", type(e).__name__)
        return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--init", action="store_true", help="data/alert_keywords.json 샘플 생성(이미 있으면 그대로)")
    ap.add_argument("--no-state", action="store_true", help="중복 방지 상태(data/alert_state.json)를 기록하지 않는 미리보기")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if a.init:
        print("생성됨:" if init_keywords_file() else "이미 있음:", KEYWORDS_PATH)
        return 0
    from generator import card_news as cn
    today = date.today()
    start, end = cn._current_display_range(today)
    rows = cn._build_summary_rows(start, end, hide_before=today)
    result = run(rows, today, update_state=not a.no_state)
    print("키워드 파일 없음/활성 키워드 없음" if result is None else f"매칭 {result['total_matches']}건 (신규 {result['new_count']}건) -> {OUTPUT_JSON}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
