"""릿링크(lit.link) 로그인 세션을 1회 수동으로 생성해 storage_state 파일로 저장.

Claude는 아이디/비밀번호를 입력하지 않는다 — 브라우저가 뜨면 직접 로그인.

실행:
    python -m scraper.litlink_login
"""
from __future__ import annotations

from config import LITLINK_STORAGE_STATE_PATH
from scraper.login_common import manual_login_and_save


def main() -> None:
    manual_login_and_save(
        "https://lit.link/en/login",
        LITLINK_STORAGE_STATE_PATH,
        "릿링크(lit.link)",
    )


if __name__ == "__main__":
    main()
