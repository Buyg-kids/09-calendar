"""[카카오 '나에게 보내기' 자동 발송] notices/kakao/<오늘(KST)>.txt 를 내 카카오톡(나와의 채팅)으로 보낸다.

오픈채팅방에 올릴 공지를 아침에 폰으로 받아 편하게 전달하려는 용도. 오픈채팅방에 직접
보내지는 않는다(카카오 API로는 불가).

카카오 '기본 템플릿 > 텍스트' 메시지는 본문이 200자까지고 넘는 부분은 잘린다(우회 방법 없음).
공지 전체(링크 3개 포함, 링크만 ~140자)는 한 통에 못 들어가므로 여러 통으로 나눠 보낸다:
  1통 제목 + 전체 달력 버튼 / 2~4통 공구 1개씩(본문=상품명·공구가, 버튼=그 공구 딥링크)
각 통은 카톡 화면에서 "전달" 버튼으로 오픈채팅방에 그대로 넘길 수 있다.

필요한 환경변수 (GitHub Actions에서는 Repository secrets):
  KAKAO_REST_API_KEY       REST API 키 (JavaScript 키와 다름)
  KAKAO_REFRESH_TOKEN      최초 1회 --exchange 로 발급한 리프레시 토큰
  KAKAO_CLIENT_SECRET      (Client Secret 사용을 켠 경우만)
  KAKAO_REDIRECT_URI       (--auth-url / --exchange 에서만 사용)
  KAKAO_NEW_REFRESH_TOKEN_FILE  (선택) 갱신 중 새 리프레시 토큰이 발급되면 이 파일에 저장

실행:
    python -m generator.send_to_me --dry-run          # 보낼 메시지만 화면에 출력 (토큰 불필요)
    python -m generator.send_to_me                    # 오늘(KST) 공지 전송
    python -m generator.send_to_me --date 2026-09-20  # 날짜 지정
    python -m generator.send_to_me --auth-url         # 최초 1회: 동의 화면 주소 만들기
    python -m generator.send_to_me --exchange <code>  # 최초 1회: 인가 코드 -> 토큰 발급
토큰 값은 절대 로그에 출력하지 않는다(--exchange 로 발급받은 본인 터미널 화면 제외).

종료 코드: 0 = 정상(공지 파일이 없거나 비어 있는 날도 0 - 경고 로그 + 내 카톡 알림만),
           1 = 토큰 갱신/전송 실패 등 진짜 오류, 2 = 환경변수·인자 설정 오류.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode

import requests

BASE_DIR = Path(__file__).resolve().parent.parent
try:  # 로컬 실행 편의용 (.env). Actions에는 .env가 없으니 없어도 무방.
    from dotenv import load_dotenv

    load_dotenv(BASE_DIR / ".env")
except ImportError:
    pass

NOTICE_DIR = BASE_DIR / "notices" / "kakao"
SITE_URL = "https://buyg-kids.github.io/09-calendar/"
KST = timezone(timedelta(hours=9))

AUTHORIZE_URL = "https://kauth.kakao.com/oauth/authorize"
TOKEN_URL = "https://kauth.kakao.com/oauth/token"
SEND_URL = "https://kapi.kakao.com/v2/api/talk/memo/default/send"

MAX_TEXT = 200
ITEM_BUTTON = "공구 보러가기"
SITE_BUTTON = "전체 공구 달력 보기"
SEND_INTERVAL_SEC = 0.6  # 메시지 순서 보존용

_URL_RE = re.compile(r"https?://\S+")


class KakaoError(RuntimeError):
    pass


def kst_today() -> date:
    return datetime.now(KST).date()


def notice_path(d: date) -> Path:
    return NOTICE_DIR / f"{d.isoformat()}.txt"


# ---------------------------------------------------------------- 공지 파싱

def _fit(text: str) -> str:
    if len(text) <= MAX_TEXT:
        return text
    print(f"[경고] 본문 {len(text)}자 -> {MAX_TEXT}자로 줄임 (카카오 제한)", file=sys.stderr)
    return text[:MAX_TEXT - 1].rstrip() + "…"


def parse_notice(raw: str) -> list[dict]:
    """공지 텍스트를 전송 단위로 나눈다: [{"text", "url", "button"}].
    kakao_notifier가 만든 형식(제목 / 1️⃣~3️⃣ 항목 / 👉 푸터)을 기준으로 하되, 직접 손본
    파일도 견디도록 블록(빈 줄 단위)별로 분류한다."""
    blocks = [b.strip() for b in re.split(r"\n\s*\n", raw.replace("\r\n", "\n").strip()) if b.strip()]
    header: list[str] = []
    others: list[list[str]] = []
    items: list[dict] = []
    footer_lines: list[str] = []
    footer_url = SITE_URL

    for b in blocks:
        lines = b.split("\n")
        link_line = next((ln for ln in lines if ln.strip().startswith("- 링크:")), None)
        if link_line:
            m = _URL_RE.search(link_line)
            body = [ln for ln in lines if ln is not link_line]
            items.append({"text": _fit("\n".join(body).strip()), "url": m.group(0) if m else SITE_URL, "button": ITEM_BUTTON})
        elif lines[0].startswith("👉"):
            m = _URL_RE.search(b)
            if m:
                footer_url = m.group(0)
            # URL은 버튼 링크로 쓰므로 본문에서는 빼고, 남는 안내 문구 끝의 ':'도 정리한다.
            footer_lines = [_URL_RE.sub("", ln).strip().rstrip(":").rstrip() for ln in lines if _URL_RE.sub("", ln).strip()]
        elif not header:
            header = lines
        else:
            others.append(lines)

    messages: list[dict] = []
    head_text = "\n".join(header)
    if footer_lines:
        head_text += "\n\n" + "\n".join(footer_lines)
    if head_text.strip():
        messages.append({"text": _fit(head_text.strip()), "url": footer_url, "button": SITE_BUTTON})
    messages.extend(items)
    for lines in others:  # 사용자가 추가로 적어 넣은 블록
        messages.append({"text": _fit("\n".join(lines)), "url": SITE_URL, "button": SITE_BUTTON})
    return messages


# ---------------------------------------------------------------- 카카오 API

def _err(r: requests.Response) -> str:
    """오류 응답에서 원인 코드/설명만 뽑는다 (본문 전체를 로그에 남기지 않기 위함)."""
    try:
        j = r.json()
    except ValueError:
        return f"HTTP {r.status_code}"
    parts = [str(j[k]) for k in ("error", "error_code", "error_description", "msg", "code") if k in j]
    return f"HTTP {r.status_code} " + " / ".join(parts)


def _client_params(rest_key: str) -> dict:
    p = {"client_id": rest_key}
    secret = os.environ.get("KAKAO_CLIENT_SECRET", "").strip()
    if secret:
        p["client_secret"] = secret
    return p


def refresh_access_token(rest_key: str, refresh_token: str) -> tuple[str, str | None]:
    data = {"grant_type": "refresh_token", "refresh_token": refresh_token, **_client_params(rest_key)}
    r = requests.post(TOKEN_URL, data=data, timeout=15)
    if r.status_code != 200:
        raise KakaoError(f"액세스 토큰 갱신 실패 - {_err(r)}")
    j = r.json()
    return j["access_token"], j.get("refresh_token")  # 리프레시 토큰은 만료 1개월 미만일 때만 새로 내려옴


def send_memo(access_token: str, text: str, url: str, button_title: str) -> None:
    template = {
        "object_type": "text",
        "text": text,
        "link": {"web_url": url, "mobile_web_url": url},
        "button_title": button_title,
    }
    r = requests.post(
        SEND_URL,
        headers={"Authorization": f"Bearer {access_token}"},
        data={"template_object": json.dumps(template, ensure_ascii=False)},
        timeout=15,
    )
    if r.status_code != 200 or r.json().get("result_code") != 0:
        raise KakaoError(f"메시지 전송 실패 - {_err(r)}")


def _save_rotated_token(new_token: str) -> None:
    path = os.environ.get("KAKAO_NEW_REFRESH_TOKEN_FILE", "").strip()
    if path:
        Path(path).write_text(new_token, encoding="utf-8")
        print("[알림] 새 리프레시 토큰이 발급되어 지정된 파일에 저장했습니다 (시크릿 갱신 필요)")
    else:
        print(
            "[경고] 리프레시 토큰이 곧 만료되어 새 토큰이 발급됐지만 저장 경로(KAKAO_NEW_REFRESH_TOKEN_FILE)가 "
            "없어 저장하지 못했습니다. 만료 전에 --auth-url / --exchange 로 다시 발급하세요.",
            file=sys.stderr,
        )


# ---------------------------------------------------------------- 최초 1회 토큰 발급 도우미

def cmd_auth_url() -> int:
    rest_key = os.environ.get("KAKAO_REST_API_KEY", "").strip()
    redirect = os.environ.get("KAKAO_REDIRECT_URI", "").strip()
    if not rest_key or not redirect:
        print("KAKAO_REST_API_KEY 와 KAKAO_REDIRECT_URI 환경변수(.env)를 먼저 설정하세요.", file=sys.stderr)
        return 2
    query = urlencode({"client_id": rest_key, "redirect_uri": redirect, "response_type": "code", "scope": "talk_message"})
    print("아래 주소를 브라우저에 붙여넣고, 로그인 + '카카오톡 메시지 전송' 동의 후")
    print("주소창에 나타나는 ?code=... 값을 복사해 --exchange 에 넣으세요:\n")
    print(f"{AUTHORIZE_URL}?{query}")
    return 0


def cmd_exchange(code: str) -> int:
    rest_key = os.environ.get("KAKAO_REST_API_KEY", "").strip()
    redirect = os.environ.get("KAKAO_REDIRECT_URI", "").strip()
    if not rest_key or not redirect:
        print("KAKAO_REST_API_KEY 와 KAKAO_REDIRECT_URI 환경변수(.env)를 먼저 설정하세요.", file=sys.stderr)
        return 2
    data = {"grant_type": "authorization_code", "redirect_uri": redirect, "code": code, **_client_params(rest_key)}
    r = requests.post(TOKEN_URL, data=data, timeout=15)
    if r.status_code != 200:
        print(f"토큰 발급 실패 - {_err(r)} (인가 코드는 1회용/10분 유효 - --auth-url 로 새로 받으세요)", file=sys.stderr)
        return 1
    j = r.json()
    scopes = (j.get("scope") or "").split()
    print("발급 완료. 아래 값을 GitHub Secrets(KAKAO_REFRESH_TOKEN)에 저장하세요.")
    print("※ 비밀번호와 같은 값입니다. 화면 공유·채팅·저장소 커밋 금지.\n")
    print(f"KAKAO_REFRESH_TOKEN={j.get('refresh_token')}")
    print(f"\n리프레시 토큰 유효기간: 약 {int(j.get('refresh_token_expires_in', 0)) // 86400}일")
    print("talk_message 동의 확인:", "OK" if "talk_message" in scopes else "누락! 콘솔 동의항목/--auth-url 의 scope 확인")
    return 0


# ---------------------------------------------------------------- 메인

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="오늘 카카오 공지를 '나에게 보내기'로 전송")
    ap.add_argument("--date", help="공지 날짜 YYYY-MM-DD (기본: 오늘 KST)")
    ap.add_argument("--dry-run", action="store_true", help="전송하지 않고 메시지만 출력")
    ap.add_argument("--auth-url", action="store_true", help="최초 1회: 동의 화면 주소 출력")
    ap.add_argument("--exchange", metavar="CODE", help="최초 1회: 인가 코드로 토큰 발급")
    args = ap.parse_args(argv)

    if args.auth_url:
        return cmd_auth_url()
    if args.exchange:
        return cmd_exchange(args.exchange)

    try:
        d = date.fromisoformat(args.date) if args.date else kst_today()
    except ValueError:
        print(f"[오류] --date 는 YYYY-MM-DD 형식이어야 합니다: {args.date!r}", file=sys.stderr)
        return 2
    path = notice_path(d)

    # "오늘 공지가 없다/못 읽는다"는 정상적으로 있을 수 있는 날이라 워크플로를 실패시키지 않는다
    # (종료 코드 0). 대신 GitHub Actions 요약에 경고로 남기고, 그 사실을 내 카톡으로도 알린다.
    # 토큰 만료·API 오류 같은 진짜 실패만 아래에서 종료 코드 1로 끝낸다.
    notice_problem = None
    messages: list[dict] = []
    if not path.exists():
        notice_problem = "공지 파일이 없어요"
    else:
        try:
            messages = parse_notice(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError) as e:
            notice_problem = f"공지 파일을 읽지 못했어요({type(e).__name__})"
        else:
            if not messages:
                notice_problem = "공지 파일이 비어 있어요"

    if notice_problem:
        print(f"::warning::{d.isoformat()} 카카오 공지 발송 건너뜀 - {notice_problem} ({path.name})")
        print(f"[경고] {notice_problem}: {path}", file=sys.stderr)
        messages = [{
            "text": _fit(f"⚠️ [Buyg] {d.isoformat()} 카카오 공지: {notice_problem}.\n어제 야간 파이프라인/배포가 정상이었는지 확인해 주세요."),
            "url": SITE_URL, "button": SITE_BUTTON,
        }]

    if args.dry_run:
        for i, m in enumerate(messages, 1):
            print(f"--- 메시지 {i}/{len(messages)} (본문 {len(m['text'])}자, 버튼 '{m['button']}') ---")
            print(m["text"])
            print(f"[버튼 링크] {m['url']}\n")
        return 0

    rest_key = os.environ.get("KAKAO_REST_API_KEY", "").strip()
    refresh_token = os.environ.get("KAKAO_REFRESH_TOKEN", "").strip()
    if not rest_key or not refresh_token:
        print("KAKAO_REST_API_KEY / KAKAO_REFRESH_TOKEN 이 설정되지 않았습니다.", file=sys.stderr)
        return 2

    try:
        access_token, new_refresh = refresh_access_token(rest_key, refresh_token)
        if new_refresh:
            _save_rotated_token(new_refresh)
        for i, m in enumerate(messages, 1):
            send_memo(access_token, m["text"], m["url"], m["button"])
            print(f"전송 완료 {i}/{len(messages)}")
            if i < len(messages):
                time.sleep(SEND_INTERVAL_SEC)
    except (KakaoError, requests.RequestException) as e:
        print(f"[오류] {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
