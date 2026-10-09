"""요금 텍스트 파서 - TourAPI(tour_collector)와 KOPIS(kopis_collector)가 같이 쓴다.

요금은 원문(fee_text/price) 그대로 보존하고, 파싱 결과만 따로 붙인다:
  fee_type  'free'(전면 무료) | 'partial'(일부 무료·일부 유료) | 'paid' | 'unknown'(정보 없음)
  is_free   fee_type == 'free'                       (카드의 [무료] 뱃지)
  fee_min   텍스트에 나온 가장 낮은 양의 금액(원), 없으면 None (0원만 나오면 0)   (카드의 '3,000원' 표기)
  fee_max   가장 높은 금액(원), 없으면 None                                      (여러 요금이면 '3,000원~')

'무료' 여부로 데이터를 버리지 않는다 - 소액 유료·요금 미기재 행사도 모두 수집하고, 표시만 달리한다.
요금 정보가 비어 있으면 무료로 단정하지 않고 'unknown'(요금 확인)으로 둔다.
"""
from __future__ import annotations

import re

_FREE_RE = re.compile(r"무\s*료|free|입장료\s*없|비용\s*없|참가비\s*없|관람료\s*없", re.IGNORECASE)
_PAID_RE = re.compile(r"유\s*료|\d[\d,]*\s*원|\d+\s*만\s*원")
_NOT_FREE_RE = re.compile(r"무\s*료\s*(?:가\s*)?(?:아님|아니|불가|제외)")
# '입장/관람/참가(료) 무료' 처럼 들어가는 비용이 무료라고 명시되면, 일부 체험·푸드트럭 등 부가 유료 언급이 있어도 무료로 본다.
_ENTRY_FREE_RE = re.compile(r"(?:입장|관람|참가|참여)\s*(?:료|비)?\s*(?:은|는)?\s*(?:전\s*면\s*)?무\s*료")

# 금액 패턴 (큰 단위부터 소비해 '1만 5천원'이 5천원으로만 잡히는 것을 막는다)
_MAN_CHEON_RE = re.compile(r"(\d+)\s*만\s*(\d+)\s*천\s*원")
_MAN_RE = re.compile(r"(\d+(?:\.\d+)?)\s*만\s*원")
_CHEON_RE = re.compile(r"(\d+)\s*천\s*원")
_WON_RE = re.compile(r"(?<![\d,.])(\d[\d,]*)\s*원")


def _amounts(text: str) -> list[int]:
    """텍스트 속 금액(원) 전부. 0원도 포함한다."""
    t = text or ""
    out: list[int] = []
    for m in _MAN_CHEON_RE.finditer(t):
        out.append(int(m.group(1)) * 10000 + int(m.group(2)) * 1000)
    t = _MAN_CHEON_RE.sub(" ", t)
    for m in _MAN_RE.finditer(t):
        out.append(int(float(m.group(1)) * 10000))
    t = _MAN_RE.sub(" ", t)
    for m in _CHEON_RE.finditer(t):
        out.append(int(m.group(1)) * 1000)
    t = _CHEON_RE.sub(" ", t)
    for m in _WON_RE.finditer(t):
        try:
            out.append(int(m.group(1).replace(",", "")))
        except ValueError:
            pass
    return out


def parse_fee_min(text: str) -> int | None:
    amounts = _amounts(text)
    if not amounts:
        return None
    positives = [a for a in amounts if a > 0]
    return min(positives) if positives else 0


def parse_fee_max(text: str) -> int | None:
    positives = [a for a in _amounts(text) if a > 0]
    return max(positives) if positives else None


def classify_fee(text: str) -> str:
    """요금 텍스트 -> 'free' / 'partial' / 'paid' / 'unknown'."""
    t = (text or "").strip()
    if not t:
        return "unknown"
    if _NOT_FREE_RE.search(t):
        return "paid"
    if _ENTRY_FREE_RE.search(t):
        return "free"
    amounts = _amounts(t)
    has_pos, has_zero = any(a > 0 for a in amounts), any(a == 0 for a in amounts)
    if has_zero and not has_pos and "유료" not in t:
        return "free"                      # '0원'
    if has_zero and has_pos:
        return "partial"                   # '성인 3,000원 / 어린이 0원'
    free, paid = bool(_FREE_RE.search(t)), bool(_PAID_RE.search(t))
    if free and paid:
        return "partial"
    if free:
        return "free"
    if paid:
        return "paid"
    return "unknown"


def describe_fee(text: str) -> dict:
    """수집기 출력에 붙일 요금 필드 묶음."""
    ftype = classify_fee(text)
    return {"fee_type": ftype, "is_free": ftype == "free", "fee_min": parse_fee_min(text), "fee_max": parse_fee_max(text)}
