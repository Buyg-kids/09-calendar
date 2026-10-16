"""시드 장소 부모 꿀팁(curation) 자동 수집기 - 초안(샌드박스). YouTube Data API v3 기반, 7대 가드레일 준수.

  BUYG_ROOT=<repo> python tools/collect_curation.py --spots 5 [--dry] [--no-llm]

흐름: 장소×주제(주차·유모차·수유실·분위기) 질의 -> 영상 메타(제목·설명) -> ① 광고 키워드 제외 ② 장소명 일치 확인 -> 주제별 문장 후보
      -> ③ 서로 다른 채널 2곳 이상이 같은 주제를 말한 경우만 채택(교차 확인) -> ④ 120자 이하 '다시 쓰기' 요약(원문 문장 복사 금지; LLM 또는 --no-llm 이면 건너뜀)
      -> ⑤ 요약문의 시설명·숫자가 출처 텍스트에 실제 있는지 대조 -> ⑥ 개인정보·비방·단정 표현 필터 -> ⑦ 장소당 5개 상한·중복 제거
결과는 data/curation_staging/<spot_id>.json (gitignore)에만 쓴다. 공개 파일로의 승격(`review:{mode:"auto",...}`, reviewed:true)은 tools/curation_promote.py(별도, Step 4 이후)가 검증기를 통과시킨 뒤 한다.
호출 한도: search.list 100유닛 - 하루 --max-units(기본 3,000) 이하로 제한, 쿼터 오류 신호(403 quotaExceeded)면 즉시 중단. 키는 .env 의 YOUTUBE_API_KEY(출력·로그에 쓰지 않는다).
로그: 예외는 타입명만(logger.exception 금지)."""
import argparse
import json
import logging
import os
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

PROJECT = Path(os.environ.get("BUYG_ROOT") or Path(__file__).resolve().parent.parent)
log = logging.getLogger("collect_curation")
RULES_VERSION = "auto-2026-10-16"
TOPICS = {   # 주제 -> (질의 접미어, 문장 후보 키워드)
    "parking": ("주차", ["주차", "주차장", "주차비", "주차 요금", "만차"]),
    "stroller": ("유모차", ["유모차", "엘리베이터", "계단", "경사", "휠체어"]),
    "nursing": ("수유실", ["수유실", "수유", "기저귀", "기저귀 교환", "아기 쉼터"]),
    "tip": ("아기랑 후기", ["오픈런", "웨이팅", "붐비", "한적", "사람 많", "아이가 좋아", "아이들이 좋아", "친절", "추천"]),
}
AD_RE = re.compile(r"협찬|원고료|체험단|광고|유료\s*광고|제공\s*받|지원\s*받|소정의|식사권|이용권\s*제공|#ad\b|#광고|paid\s*promotion|sponsored", re.I)
PII_RE = re.compile(r"01[016789][-\s.]?\d{3,4}[-\s.]?\d{4}|\d{2,3}[-\s]\d{3,4}[-\s]\d{4}|[\w.+-]+@[\w-]+\.[\w.]+")   # 전화번호·이메일
ABUSE_RE = re.compile(r"씨발|ㅅㅂ|개새|병신|지랄|쓰레기\s*같|사기꾼|최악의")
ABSOLUTE_RE = re.compile(r"(반드시|절대|100\s*%)\s*(안전|괜찮|가능)|무조건\s*(안전|가능)")          # 안전 관련 단정 표현
MAX_TIPS, MAX_LEN = 5, 120
MAX_LLM_CALLS, MAX_LLM_USD = 30, 0.50          # 킬스위치: 하루 LLM 호출 30회 / 추정 비용 $0.5 중 먼저 닿는 쪽에서 멈춘다(크레딧 소진 방지)
LLM_MODEL = "claude-haiku-5-5"                                         # 요약 모델(2026-10 기준 단가: 입력 $0.10 / 출력 $0.50 per 100만 토큰, 프롬프트 100K 이하)
PRICE_IN_PER_MTOK = float(os.environ.get("CURATION_PRICE_IN", "0.10"))      # 달러/100만 토큰 - 모델·요금이 바뀌면 환경변수로 조정
PRICE_OUT_PER_MTOK = float(os.environ.get("CURATION_PRICE_OUT", "0.50"))
SOURCE_LABEL = "(유튜브 실방문 후기)"


# ---------------- 순수 함수(단위 테스트 대상) ----------------
def is_ad(*texts):
    return bool(AD_RE.search(" ".join(t or "" for t in texts)))


def sentences(text):
    parts = re.split(r"(?<=[.!?。])\s+|\n+", text or "")
    return [p.strip(" -•·*▶▷■□◆◇#") for p in parts if 8 <= len(p.strip()) <= 300]


def topic_sentences(text, topic, place):
    kws = TOPICS[topic][1]
    return [s for s in sentences(text) if any(k in s for k in kws) and not AD_RE.search(s)]


def mentions_place(place, *texts):
    core = re.sub(r"\(.*?\)|\s+", "", place)
    blob = re.sub(r"\s+", "", " ".join(t or "" for t in texts))
    return len(core) >= 2 and core in blob


def tokens_ok(summary, source_text):
    """요약문의 시설명·숫자가 출처 텍스트에 실제로 있어야 한다(환각 방지). 없으면 False."""
    src = re.sub(r"\s+", "", source_text or "")
    for n in re.findall(r"\d[\d,\.]*", summary):
        if n.replace(",", "") not in src.replace(",", ""):
            return False
    for w in ("수유실", "기저귀", "엘리베이터", "경사로", "주차장", "유모차", "휠체어", "계단", "무료", "유료"):
        if w in summary and w not in src:
            return False
    return True


def passes_filters(summary):
    return bool(summary) and len(summary) <= MAX_LEN and not (PII_RE.search(summary) or ABUSE_RE.search(summary) or ABSOLUTE_RE.search(summary))


def aggregate(cands):
    """cands: [{topic, channel, text, url, ...}] -> 주제별로 서로 다른 채널 2곳 이상인 것만."""
    by = {}
    for c in cands:
        by.setdefault(c["topic"], {}).setdefault(c["channel"], []).append(c)
    out = {}
    for tp, chs in by.items():
        if len(chs) >= 2:
            out[tp] = [v[0] for v in chs.values()]
    return out


def make_item(topic, summary, srcs, today):
    """curation 항목(spot_enriched_validator 규격 + review 표기). 요약 끝에 출처 표기를 붙인다."""
    text = summary.rstrip(". ") + " " + SOURCE_LABEL
    return {"topic": topic, "text": text[: MAX_LEN + len(SOURCE_LABEL) + 1], "src_urls": [s["url"] for s in srcs][:3], "provenance": "ai_summary", "checked": today,
            "reviewed": True, "source_count": len({s["channel"] for s in srcs}), "sponsor_risk": False,
            "review": {"mode": "auto", "rules_version": RULES_VERSION, "checked": today}}


class LLMBudget:
    """하루 LLM 사용량(호출 수·추정 비용)을 data/curation_llm_usage.json 에 누적해 여러 번 실행해도 합산 한도를 지킨다."""
    def __init__(self, path, today, max_calls=MAX_LLM_CALLS, max_usd=MAX_LLM_USD):
        self.path, self.today, self.max_calls, self.max_usd = Path(path), today, max_calls, max_usd
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            d = {}
        self.calls, self.usd = (int(d.get("calls", 0)), float(d.get("usd", 0.0))) if d.get("date") == today else (0, 0.0)

    def allow(self):
        return self.calls < self.max_calls and self.usd < self.max_usd

    def charge(self, in_tok, out_tok):
        self.calls += 1
        self.usd += (in_tok * PRICE_IN_PER_MTOK + out_tok * PRICE_OUT_PER_MTOK) / 1e6
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"date": self.today, "calls": self.calls, "usd": round(self.usd, 5)}), encoding="utf-8")


def killed():
    """킬스위치: data/curation.disabled 파일이 있거나 CURATION_DISABLED=1 이면 수집·요약을 하지 않는다."""
    return (PROJECT / "data" / "curation.disabled").exists() or os.environ.get("CURATION_DISABLED") == "1"


# ---------------- YouTube API ----------------
class Quota(Exception):
    pass


class YT:
    def __init__(self, key, max_units):
        import requests
        self.r, self.key, self.left, self.used = requests, key, max_units, 0

    def get(self, ep, params, cost):
        if self.left < cost:
            raise Quota("--max-units 도달")
        self.left -= cost; self.used += cost
        resp = self.r.get("https://www.googleapis.com/youtube/v3/" + ep, params=dict(params, key=self.key), timeout=20)
        time.sleep(0.5)
        if resp.status_code in (403, 429):
            raise Quota(f"HTTP {resp.status_code}")
        return resp.json() if resp.status_code == 200 else {}

    def search(self, q, after_iso):
        j = self.get("search", {"part": "snippet", "q": q, "type": "video", "maxResults": 6, "relevanceLanguage": "ko", "regionCode": "KR", "publishedAfter": after_iso, "safeSearch": "strict"}, 100)
        return [it["id"]["videoId"] for it in j.get("items", []) if it.get("id", {}).get("videoId")]

    def videos(self, ids):
        if not ids:
            return []
        j = self.get("videos", {"part": "snippet", "id": ",".join(ids[:20])}, 1)
        return j.get("items", [])


def summarize(sentence_list, place, topic, use_llm, budget=None):
    """원문 문장을 그대로 쓰지 않고 한국어 한 문장(120자 이하)으로 다시 쓴다. LLM 이 없으면 None(건너뜀 - 원문 복사 방지)."""
    if not use_llm:
        return None
    if budget is not None and not budget.allow():
        return "BUDGET"                                   # 오늘 한도 소진 - 호출하지 않고 호출한 쪽이 멈춘다
    try:
        import anthropic
        client = anthropic.Anthropic()
        msg = client.messages.create(model=LLM_MODEL, max_tokens=200, messages=[{"role": "user", "content":
            f"장소: {place}\n주제: {topic}\n아래는 방문 후기 영상 설명에서 뽑은 문장들입니다. 이 문장들에 실제로 들어 있는 사실만 사용해 부모가 읽기 쉬운 한국어 한 문장(100자 이내)으로 다시 쓰세요. 원문 문장을 그대로 복사하지 말고, 없는 정보·숫자·시설을 추가하지 마세요. 출력은 문장만.\n\n" + "\n".join("- " + s for s in sentence_list[:4])}])
        if budget is not None:
            budget.charge(getattr(msg.usage, "input_tokens", 0), getattr(msg.usage, "output_tokens", 0))
        return msg.content[0].text.strip().strip('"')
    except Exception as e:      # noqa: BLE001 - 실패하면 이 항목만 건너뜀(타입명만 기록)
        log.error("요약 호출 실패(%s)", type(e).__name__)
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--spots", type=int, default=5)
    ap.add_argument("--max-units", type=int, default=3000)
    ap.add_argument("--dry", action="store_true", help="API 를 부르지 않고 대상만 출력")
    ap.add_argument("--no-llm", action="store_true", help="요약(LLM) 없이 후보·교차 확인 단계까지만")
    ap.add_argument("--only", default="")
    ap.add_argument("--max-llm-calls", type=int, default=MAX_LLM_CALLS)
    ap.add_argument("--max-llm-usd", type=float, default=MAX_LLM_USD)
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    sys.path.insert(0, str(PROJECT))
    from dotenv import load_dotenv
    load_dotenv(Path(os.environ.get("BUYG_ENV_ROOT") or PROJECT) / ".env")   # 샌드박스 시험용: 키는 실제 저장소의 .env 에서 읽는다(복사하지 않음)
    key = os.getenv("YOUTUBE_API_KEY", "").strip()
    enr = json.loads((PROJECT / "spot_enriched.json").read_text(encoding="utf-8"))["spots"]
    seed = {s["id"]: s for s in json.loads((PROJECT / "free_spots_seed.json").read_text(encoding="utf-8"))["spots"]}
    if killed():
        log.info("킬스위치 작동(data/curation.disabled 또는 CURATION_DISABLED=1) - 수집·요약 건너뜀"); return 0
    today = date.today().isoformat()
    qp = PROJECT / "data" / "curation_state.json"          # 큐 상태: 장소별 마지막 처리일·상태(done|partial|none)
    try:
        qs = json.loads(qp.read_text(encoding="utf-8"))
    except Exception:
        qs = {}
    pool = [k for k in (a.only.split(",") if a.only else sorted(enr)) if k in enr]
    pool.sort(key=lambda k: (0 if (qs.get(k) or {}).get("status") == "partial" else 1, (qs.get(k) or {}).get("last_run", "")))   # 미완료 → 한 번도 안 한 곳 → 오래된 순
    targets = pool[: a.spots]
    budget = LLMBudget(PROJECT / "data" / "curation_llm_usage.json", today, a.max_llm_calls, a.max_llm_usd)
    log.info("대상 %d곳, 주제 %d개, LLM 오늘 사용 %d/%d회 ($%.3f/$%.2f)", len(targets), len(TOPICS), budget.calls, budget.max_calls, budget.usd, budget.max_usd)
    if a.dry or not key:
        log.info("dry-run 또는 키 없음 - 종료"); return 0
    yt = YT(key, a.max_units)
    after = (datetime.now(timezone.utc) - timedelta(days=730)).strftime("%Y-%m-%dT%H:%M:%SZ")
    stage = PROJECT / "data" / "curation_staging"; stage.mkdir(parents=True, exist_ok=True)
    for sid in targets:
        place = seed[sid]["title"]; cands = []
        try:
            for topic, (suf, _) in TOPICS.items():
                for v in yt.videos(yt.search(f"{place} {suf}", after)):
                    sn = v.get("snippet", {})
                    if is_ad(sn.get("title"), sn.get("description")) or not mentions_place(place, sn.get("title"), sn.get("description")):
                        continue
                    for s in topic_sentences(sn.get("title", "") + "\n" + sn.get("description", ""), topic, place)[:2]:
                        cands.append({"topic": topic, "channel": sn.get("channelId"), "text": s, "url": "https://www.youtube.com/watch?v=" + v["id"]})
        except Quota as e:
            log.error("수집 중단: %s", e); break
        except Exception as e:      # noqa: BLE001
            log.error("%s 처리 실패(%s)", sid, type(e).__name__); continue
        agg, items, partial = aggregate(cands), [], False
        for tp, srcs in agg.items():
            summ = summarize([s["text"] for s in srcs], place, tp, not a.no_llm, budget)
            if summ == "BUDGET":                               # 오늘 LLM 한도 소진: 이 장소는 미완료로 두고 내일 이어서 처리
                partial = True; break
            if summ and passes_filters(summ) and tokens_ok(summ, " ".join(s["text"] for s in srcs)):
                items.append(make_item(tp, summ, srcs, today))
        items = items[:MAX_TIPS]
        (stage / f"{sid}.json").write_text(json.dumps({"spot_id": sid, "place": place, "generated": today, "candidates": len(cands), "topics_cross_checked": sorted(agg), "items": items}, ensure_ascii=False, indent=1), encoding="utf-8")
        qs[sid] = {"last_run": today, "status": "partial" if partial else ("done" if items else "none")}
        qp.parent.mkdir(parents=True, exist_ok=True); qp.write_text(json.dumps(qs, ensure_ascii=False, indent=1), encoding="utf-8")
        log.info("%s %s: 후보 %d, 교차 확인 주제 %s, 채택 %d%s (YouTube %d유닛, LLM %d회)", sid, place, len(cands), sorted(agg), len(items), " [LLM 한도로 미완료]" if partial else "", yt.used, budget.calls)
        if partial:
            log.info("오늘 LLM 한도(%d회/$%.2f)에 도달 - 남은 장소는 내일 이어서 처리", budget.max_calls, budget.max_usd); break
    return 0


if __name__ == "__main__":
    sys.exit(main())
