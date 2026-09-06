"""서비스 공통 수동 로그인 세션 저장 로직.

세 개(인스타그램/인포크/릿링크) 로그인 스크립트가 동일한 패턴을 쓴다:
브라우저를 띄우고 -> 사람이 직접 로그인 -> storage_state 저장.
Claude/자동화 코드는 아이디·비밀번호를 절대 입력하거나 저장하지 않는다.
"""
from __future__ import annotations

from pathlib import Path

from playwright.sync_api import sync_playwright


def manual_login_and_save(login_url: str, storage_state_path: Path, service_label: str) -> None:
    storage_state_path.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto(login_url)
        print(f"브라우저에서 {service_label}에 직접 로그인하세요.")
        input("로그인이 끝나고 로그인된 화면이 보이면 이 터미널에서 Enter를 누르세요...")
        context.storage_state(path=str(storage_state_path))
        print(f"{service_label} 로그인 세션 저장 완료: {storage_state_path}")
        browser.close()
