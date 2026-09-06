"""인포크링크 로그인 세션을 1회 수동으로 생성해 storage_state 파일로 저장.

브라우저가 뜨면 우측 상단 로그인 버튼으로 직접 로그인하세요
(정확한 /login 경로가 서비스 업데이트로 바뀔 수 있어 홈으로 진입시킨다).
Claude는 아이디/비밀번호를 입력하지 않는다.

실행:
    python -m scraper.inpock_login
"""
from __future__ import annotations

from config import INPOCK_STORAGE_STATE_PATH
from scraper.login_common import manual_login_and_save


def main() -> None:
    manual_login_and_save(
        "https://link.inpock.co.kr/",
        INPOCK_STORAGE_STATE_PATH,
        "인포크링크",
    )


if __name__ == "__main__":
    main()
