"""저비용 로컬 사전필터.

Claude 호출 전에 명백히 무관한 텍스트(공구/할인/링크 등 신호가 전혀 없는
일반 잡담, UI 텍스트 등)를 걸러 API 비용을 줄인다. 판단이 애매한 경우는
전부 통과시켜 최종 판단은 항상 parser/extract_schedule.py 의 Claude 호출이
내리도록 한다 (엄격 제외 기준은 프롬프트 쪽에서 강제).
"""
from __future__ import annotations

from config import CATEGORIES

GROUP_BUY_SIGNAL_WORDS = [
    "공구", "공동구매", "단독가", "특가", "할인가", "할인코드", "프로필 링크",
    "링크 클릭", "구매링크", "구매 링크", "오픈", "마감", "선착순", "품절",
    "리오더", "재입고", "쿠폰", "이벤트가", "런칭가",
]

MIN_TEXT_LENGTH = 8


def _all_keywords() -> list[str]:
    kws: list[str] = []
    for cat in CATEGORIES.values():
        kws.extend(cat["keywords"])
    return kws


_ALL_KEYWORDS = _all_keywords()


def quick_prefilter(text: str) -> bool:
    """True 면 Claude 호출 대상, False 면 명백히 무관하므로 건너뜀."""
    if not text or len(text.strip()) < MIN_TEXT_LENGTH:
        return False
    haystack = text.replace(" ", "")
    for word in GROUP_BUY_SIGNAL_WORDS:
        if word.replace(" ", "") in haystack:
            return True
    for kw in _ALL_KEYWORDS:
        if kw.replace(" ", "") in haystack:
            return True
    return False
