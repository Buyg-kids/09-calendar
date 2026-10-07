"""저비용 로컬 사전필터.

Claude 호출 전에 명백히 무관한 텍스트(공구/할인/링크 등 신호가 전혀 없는
일반 잡담, UI 텍스트 등)를 걸러 API 비용을 줄인다. 판단이 애매한 경우는
전부 통과시켜 최종 판단은 항상 parser/extract_schedule.py 의 Claude 호출이
내리도록 한다 (엄격 제외 기준은 프롬프트 쪽에서 강제).
"""
from __future__ import annotations

import re

GROUP_BUY_SIGNAL_WORDS = [
    "공구", "공동구매", "단독가", "특가", "할인가", "할인", "할인코드", "프로필 링크",
    "프로필", "링크 클릭", "구매링크", "구매 링크", "구매", "주문", "오픈", "마감",
    "선착순", "품절", "리오더", "재입고", "쿠폰", "이벤트가", "이벤트", "런칭가",
    "마켓", "최저가", "링크", "무료배송",
]

MIN_TEXT_LENGTH = 8


def is_experience_candidate(text: str) -> bool:
    """체험/여행/숙소 키워드가 있으면 True - quick_prefilter를 통과한 글에 한해
    Claude에 '체험 후보' 힌트를 붙이는 용도(통과 여부에는 영향 없음)."""
    from config import EXPERIENCE_HINT_KEYWORDS
    haystack = (text or "").replace(" ", "")
    return any(kw in haystack for kw in EXPERIENCE_HINT_KEYWORDS)


_CTA_RES: list | None = None
_SCHEDULE_RES: list | None = None


def _compiled_patterns() -> tuple[list, list]:
    """config 의 COMMENT_CTA_PATTERNS / SCHEDULE_SIGNAL_PATTERNS 를 한 번만 컴파일."""
    global _CTA_RES, _SCHEDULE_RES
    if _CTA_RES is None or _SCHEDULE_RES is None:
        from config import COMMENT_CTA_PATTERNS, SCHEDULE_SIGNAL_PATTERNS
        _CTA_RES = [re.compile(p, re.IGNORECASE) for p in COMMENT_CTA_PATTERNS]
        _SCHEDULE_RES = [re.compile(p, re.IGNORECASE) for p in SCHEDULE_SIGNAL_PATTERNS]
    return _CTA_RES, _SCHEDULE_RES


def cta_schedule_pass(text: str) -> bool:
    """'행동 유도(댓글에 링크/저요, 프로필 링크 등)' + '일정·상업 신호(OPEN/마감/날짜 범위)'가 둘 다 있으면 True."""
    cta, sched = _compiled_patterns()
    return any(r.search(text) for r in cta) and any(r.search(text) for r in sched)


def legacy_prefilter(text: str) -> bool:
    """2026-10-07 이전 규칙(공구 신호어 / 체험 패스 키워드) - 회귀 비교·테스트용으로 분리해 둔 원래 로직."""
    if not text or len(text.strip()) < MIN_TEXT_LENGTH:
        return False
    haystack = text.replace(" ", "")
    if any(word.replace(" ", "") in haystack for word in GROUP_BUY_SIGNAL_WORDS):
        return True
    from config import EXPERIENCE_PASS_KEYWORDS
    return any(kw in haystack for kw in EXPERIENCE_PASS_KEYWORDS)


def quick_prefilter(text: str) -> bool:
    """True 면 Claude 호출 대상, False 면 명백히 무관하므로 건너뜀.

    이전에는 카테고리 키워드(기저귀/장난감 등) 하나만 있어도 통과시켰는데,
    그러면 공구가 아닌 일상 글("오늘 기저귀 샀어요 완전 좋아요")까지 전부
    Claude로 넘어가 비용이 크게 샜다 - 타겟 계정 340개 기준 실측해보니 이
    분기 하나가 전체 통과율의 상당 부분을 차지했다. 공구 신호어가 최소
    하나는 있어야 통과하도록 강화 - 카테고리 키워드는 더 이상 단독 통과
    조건이 아니다 (신호어 없이 카테고리 키워드만 있는 캡션은 공구가 아닐
    확률이 압도적으로 높다)."""
    if legacy_prefilter(text):
        return True
    # 2026-10-07: 공구 해시태그/신호어가 없어도 '댓글에 링크/저요' 같은 행동 유도 + 오픈/마감/날짜 범위가 함께 있으면 통과
    return bool(text) and cta_schedule_pass(text)
