"""0~7세 연령대 태깅 (키워드/숫자 룰 기반) - 홈의 '연령별 인기공구' 섹션이 쓴다.

연령대(AGE_GROUPS):  baby 0~1세(0~23개월) · toddler 2~3세 · kid 4~5세 · preschool 6~7세
입력 텍스트는 상품명 + 브랜드 + 혜택 요약(key_benefit). 한 상품이 여러 연령대에 걸칠 수 있다(예: '3~5세' -> toddler, kid).

규칙 우선순위
 1) 명시된 나이/개월수  -  '3세', '만 4세', '3~5세', '5세 이상'(7세까지), '6세 이하'(0세부터), '24개월', '6~12개월', '3살'
    ('세트', '세일' 처럼 숫자+세 뒤에 글자가 붙는 오탐은 걸러낸다)
 2) 명시가 없으면 키워드(신생아·모빌·젖병 -> baby, 토들러·배변훈련 -> toddler, 유치원 -> kid, 예비초등·입학 -> preschool 등)
 3) 둘 다 없으면 [] (연령 미표기 - '전연령'으로 취급, 연령별 섹션에는 노출하지 않는다)
순수 함수라 네트워크/DB 없이 테스트된다. 야간 빌드(card_news._build_summary_rows)가 행마다 age_groups 필드로 붙인다.
"""
from __future__ import annotations

import re

AGE_GROUPS = [
    ("baby", "0~1세 베이비", 0, 1),
    ("toddler", "2~3세 토들러", 2, 3),
    ("kid", "4~5세 키즈", 4, 5),
    ("preschool", "6~7세 프리스쿨", 6, 7),
]
_KEYS = [g[0] for g in AGE_GROUPS]
MAX_AGE = 7

# 숫자 + 세/살 (뒤에 '트'(세트), '일'(세일), '탁'(세탁), '제'(세제), '척' 등이 붙으면 나이가 아니다)
_AGE = r"(\d{1,2})\s*(?:세|살)(?![트일탁제척정대])"
_RANGE_AGE = re.compile(r"(\d{1,2})\s*[~\-–—]\s*(\d{1,2})\s*(?:세|살)(?![트일탁제척정대])")
_SINGLE_AGE = re.compile(r"(?<![\d~\-–—])" + _AGE)
_OVER = re.compile(_AGE + r"\s*(?:이상|부터|~|이후)")
_UNDER = re.compile(_AGE + r"\s*(?:이하|까지|미만)")
_RANGE_MONTH = re.compile(r"(\d{1,3})\s*[~\-–—]\s*(\d{1,3})\s*개월")
_SINGLE_MONTH = re.compile(r"(?<![\d~\-–—])(\d{1,3})\s*개월")

# 명시된 나이가 없을 때만 쓰는 키워드 (좁게 잡는다 - '유아'/'아기'처럼 0~7세 전체를 가리키는 말은 넣지 않는다)
_KEYWORDS = {
    "baby": ["신생아", "영아", "베이비", "모빌", "치발기", "젖병", "분유", "유축", "속싸개", "겉싸개", "아기띠", "100일", "백일", "돌잔치",
             "첫돌", "뒤집기", "쪽쪽이", "바운서", "초기이유식", "중기이유식", "후기이유식", "이유식", "수유", "아기욕조", "배냇"],
    "toddler": ["토들러", "걸음마", "배변훈련", "유아변기", "두돌", "세돌", "유아식기", "완료기"],
    "kid": ["유치원", "어린이집", "키즈카페", "어린이용", "미취학"],
    "preschool": ["예비초등", "초등", "입학", "한글", "책가방", "학습지", "연산", "학습"],
}


def _span(lo: int, hi: int) -> set[str]:
    """나이 범위 [lo, hi] 와 겹치는 연령대 키 집합."""
    lo, hi = max(0, lo), min(MAX_AGE, hi)
    return {k for k, _name, a, b in AGE_GROUPS if lo <= b and hi >= a} if lo <= hi else set()


def _month_to_age(m: int) -> int:
    return min(MAX_AGE, m // 12)


def explicit_ages(text: str) -> set[str]:
    """텍스트에 적힌 나이/개월수에서 연령대 집합. 없으면 빈 집합."""
    return _explicit(text)[0]


def _explicit(text: str) -> tuple[set[str], bool]:
    """(연령대 집합, 나이 표기가 하나라도 있었는지). 나이 표기가 있었지만 0~7세 밖(예: '10세 이상')이면 ({}, True) -
    이 경우 키워드 폴백으로 넘어가지 않는다('12~14세 초등 고학년'의 '초등'이 preschool 로 잡히지 않게)."""
    t = text or ""
    out: set[str] = set()
    used: list[tuple[int, int]] = []

    def free(m) -> bool:
        return not any(m.start() < e and m.end() > s for s, e in used)

    for m in _RANGE_AGE.finditer(t):
        a, b = sorted((int(m.group(1)), int(m.group(2))))
        if b <= 19 and a <= MAX_AGE:                   # 12~14세 같은 초등 고학년 이상은 제외
            out |= _span(a, b)
        used.append((m.start(), m.end()))
    for m in _RANGE_MONTH.finditer(t):
        a, b = sorted((int(m.group(1)), int(m.group(2))))
        out |= _span(_month_to_age(a), _month_to_age(b))
        used.append((m.start(), m.end()))
    for m in _OVER.finditer(t):
        if free(m):
            n = int(m.group(1))
            if n <= MAX_AGE:
                out |= _span(n, MAX_AGE)
            used.append((m.start(), m.end()))
    for m in _UNDER.finditer(t):
        if free(m):
            n = int(m.group(1))
            if n <= 19:
                out |= _span(0, min(n, MAX_AGE))
            used.append((m.start(), m.end()))
    for m in _SINGLE_AGE.finditer(t):
        if free(m):
            n = int(m.group(1))
            if n <= MAX_AGE:
                out |= _span(n, n)
            used.append((m.start(), m.end()))
    for m in _SINGLE_MONTH.finditer(t):
        if free(m):
            out |= _span(_month_to_age(int(m.group(1))), _month_to_age(int(m.group(1))))
            used.append((m.start(), m.end()))
    return out, bool(used)


# 연령 폭이 넓은 일반어 - 위 '강한 키워드'도 명시 나이도 없을 때만, 일반적으로 통하는 연령대로 매핑한다(오탐 가능성을 알고 쓰는 약한 신호).
_SOFT_KEYWORDS = [
    (["유아", "영유아"], ("toddler", "kid")),
    (["어린이", "키즈", "kids"], ("kid", "preschool")),
]


def keyword_ages(text: str) -> set[str]:
    compact = re.sub(r"\s+", "", text or "")
    strong = {k for k, words in _KEYWORDS.items() if any(w in compact for w in words)}
    if strong:
        return strong
    low = compact.lower()
    soft: set[str] = set()
    for words, groups in _SOFT_KEYWORDS:
        if any(w in low for w in words):
            soft |= set(groups)
    return soft


def age_groups(text: str) -> list[str]:
    """연령대 키 목록(baby/toddler/kid/preschool 순). 명시 나이가 우선, 없으면 키워드, 없으면 []."""
    found, had_explicit = _explicit(text)
    if not had_explicit:
        found = keyword_ages(text)
    return [k for k in _KEYS if k in found]


def age_groups_for_row(row: dict) -> list[str]:
    return age_groups(" ".join(str(row.get(k) or "") for k in ("product_name", "brand", "key_benefit")))
