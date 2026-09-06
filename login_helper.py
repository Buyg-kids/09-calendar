"""인스타그램 로그인 쿠키 저장 헬퍼.

브라우저 창이 뜨면 사용자가 직접 인스타그램에 로그인한다. Claude(자동화 코드)는
아이디/비밀번호를 절대 입력하거나 어디에도 저장하지 않는다.

쿠키 저장은 둘 중 먼저 일어나는 쪽으로 트리거된다:
  1) 로그인 완료 자동 감지 (로그인 폼이 사라지고 로그인 페이지를 벗어나면)
  2) 터미널에서 Enter 입력 (수동으로 바로 저장하고 싶을 때)
어느 쪽이든 감지 즉시 쿠키를 data/insta_cookies.json 에 저장하고 브라우저를
닫으므로, "Enter 누르기 전에 창을 닫아서 저장이 누락되는" 문제가 없다.

scraper/insta_scraper.py 가 이 쿠키 파일을 로드해 로그인 상태로 피드/릴스
캡션을 수집하는 데 사용한다. 세션은 언젠가 만료되니, 스크래핑이 다시 로그인
요구를 받기 시작하면 이 스크립트를 한 번 더 실행해 쿠키를 갱신하면 된다.

실행:
    python login_helper.py
"""
from __future__ import annotations

import json
import threading

from playwright.sync_api import sync_playwright

from config import INSTA_COOKIES_PATH

POLL_INTERVAL_SEC = 1.5
MAX_WAIT_SEC = 900  # 15분


def _wait_for_enter(event: threading.Event) -> None:
    try:
        input()
    except EOFError:
        # stdin이 연결돼 있지 않은 환경(예: 백그라운드 실행)에서는 input()이
        # 실제 Enter 없이도 즉시 EOFError를 낸다 - 이걸 Enter로 착각하면 안 되므로
        # 이벤트를 세팅하지 않고 조용히 종료한다. 이 경우 자동 감지 루프만으로 판단한다.
        return
    event.set()


def _looks_logged_in(page) -> bool:
    url = page.url
    if "accounts/login" in url or "challenge" in url or "checkpoint" in url:
        return False
    try:
        # 로그인 폼(아이디 입력창)이 더 이상 안 보이면 로그인된 것으로 간주
        return page.locator("input[name='username']").count() == 0
    except Exception:
        return False


def _save_cookies(context) -> bool:
    """저장하고, 실제로 로그인된 세션인지(sessionid 쿠키 존재) 여부를 반환한다."""
    INSTA_COOKIES_PATH.parent.mkdir(parents=True, exist_ok=True)
    cookies = context.cookies()
    INSTA_COOKIES_PATH.write_text(
        json.dumps(cookies, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    has_session = any(c.get("name") == "sessionid" for c in cookies)
    if has_session:
        print(f"쿠키 저장 완료: {INSTA_COOKIES_PATH} (로그인 세션 확인됨, 쿠키 {len(cookies)}개)")
    else:
        print(
            f"쿠키를 저장했지만 로그인 세션(sessionid)이 없습니다: {INSTA_COOKIES_PATH}\n"
            "  -> 로그인이 완료되기 전에 저장된 것 같습니다. 브라우저에서 인스타그램 피드가\n"
            "     실제로 보이는 상태를 확인한 뒤 다시 이 스크립트를 실행해주세요."
        )
    return has_session


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto("https://www.instagram.com/accounts/login/")

        print("브라우저에서 인스타그램에 직접 로그인하세요.")
        print("로그인이 끝나면 자동으로 감지해서 쿠키를 저장합니다.")
        print("(바로 저장하고 싶으면 이 터미널에서 Enter를 눌러도 됩니다.)")

        enter_event = threading.Event()
        threading.Thread(target=_wait_for_enter, args=(enter_event,), daemon=True).start()

        elapsed = 0.0
        detected = False
        while elapsed < MAX_WAIT_SEC:
            if enter_event.is_set():
                print("Enter 입력을 확인했습니다.")
                detected = True
                break
            try:
                if _looks_logged_in(page):
                    print("로그인 완료를 자동으로 감지했습니다.")
                    detected = True
                    break
            except Exception:
                pass
            page.wait_for_timeout(int(POLL_INTERVAL_SEC * 1000))
            elapsed += POLL_INTERVAL_SEC

        if not detected:
            print("시간이 오래 지나 자동 감지를 멈췄습니다. 지금까지의 쿠키를 저장합니다.")

        _save_cookies(context)
        browser.close()


if __name__ == "__main__":
    main()
