"""경기 남부 앵커 후보 발굴(수원·용인·화성 우선).  python tools/discover_anchor_area.py [--max-calls 80] [--cache-dir data/theme_scan_cache] [--top 20] [--out docs/anchor_discovery_swyhhs]

TourAPI `areaBasedList2`(관광지 12·문화시설 14·레포츠 28)를 시군구 코드로 훑어(`ldongCode2` 로 확인한 경기 코드) 그 시의 장소를 통째로 모은 뒤, 아이 동반 적합도 점수로 시별 상위 후보를 뽑는다.
 - 점수는 '가이드를 먼저 만들 가치'를 가늠하는 내부 우선순위일 뿐 화면에 순위로 노출하지 않는다(순위·추천 단정 금지).
 - 사실(운영시간·요금 등)은 여기 없다 - 가이드 작성 단계에서 TourAPI 상세(detailIntro2)와 공식 홈페이지로 채운다.
 - 원본 응답은 --cache-dir 에 저장(같은 요청은 호출하지 않음). 인증키는 .env, 출력에 쓰지 않는다. 예외는 타입명만(Traceback 금지).
산출: <out>.json (기계용) + <out>.md (사람이 보는 표)."""
import argparse
import json
import os
import re
import sys
from pathlib import Path

PROJECT = Path(os.environ.get("BUYG_ROOT") or Path(__file__).resolve().parent.parent)
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tools"))

# 경기 시군구 코드(ldongCode2 로 확인, 2026-10-20). 수원·용인은 구 단위 코드로만 조회된다.
CITIES = {"수원시": ["111", "113", "115", "117"], "용인시": ["461", "463", "465"], "화성시": ["590"]}
TYPES = (12, 14, 28)
STRONG = re.compile(r"어린이|키즈|아이|동물|곤충|목장|농장|팜|체험|과학|천문|박물관|식물원|수목원|테마파크|랜드|에버랜드|민속촌|상상의숲|수족|아쿠아|썰매|조랑말|딸기")
MEDIUM = re.compile(r"공원|호수|숲|생태|미술관|도서관|궁|성곽|행궁|화성|융건릉|갯벌|습지|정원|파크|리조트")
FLAGSHIP = re.compile(r"에버랜드|민속촌|수원화성|화성행궁|융건릉|한택식물원|아쿠아플라넷|농업박물관|어린이박물관|상상의숲|곤충테마파크|농촌테마파크")   # 부모 검색량이 큰 대표 장소(이름 기준 가중, 순서용)
EXCLUDE = re.compile(r"체육공원|운동장|스타디움|경기장|캠핑|야영|교회|성당|사찰|묘|고택|벽화|거리$|마을$|대학교|병원|터미널|역$|펫파크|반려|스피드웨이|골프|CC$|연수원|수련원")


def kid_score(title, has_photo=False, ctype=""):
    """아이 동반 적합도 내부 점수(순수 함수, 테스트 대상). 제외 패턴이면 -1."""
    if EXCLUDE.search(title or ""):
        return -1
    return (3 if FLAGSHIP.search(title or "") else 0) - (2 if "도서관" in (title or "") else 0) + (3 if STRONG.search(title or "") else 0) + (1 if MEDIUM.search(title or "") else 0) + (1 if has_photo else 0) + (1 if str(ctype) == "14" else 0)


def collect(budget, key, cities=None, types=TYPES, max_pages=3):
    """시별 {contentid: 원본 항목}. 호출 한도·쿼터 신호는 budget 이 처리한다."""
    out = {}
    for city, codes in (cities or CITIES).items():
        for code in codes:
            for ct in types:
                for page in range(1, max_pages + 1):
                    items = budget.call(key, "areaBasedList2", {"lDongRegnCd": "41", "lDongSignguCd": code, "contentTypeId": ct, "numOfRows": 100, "pageNo": page, "arrange": "A"}) or []
                    for it in items:
                        out.setdefault(city, {})[str(it.get("contentid"))] = it
                    if len(items) < 100:
                        break
    return out


def pick(raw, top=20):
    rows = {}
    for city, m in raw.items():
        lst = []
        for cid, it in m.items():
            title = (it.get("title") or "").strip()
            s = kid_score(title, bool(it.get("firstimage")), it.get("contenttypeid"))
            if s <= 0:
                continue
            try:
                geo = {"lat": round(float(it["mapy"]), 6), "lon": round(float(it["mapx"]), 6)}
            except (KeyError, TypeError, ValueError):
                geo = None
            lst.append({"contentid": cid, "title": title, "address": (it.get("addr1") or "").strip(), "city": city, "contenttypeid": str(it.get("contenttypeid") or ""), "photo": bool(it.get("firstimage")), "geo": geo, "score": s})
        lst.sort(key=lambda r: (-r["score"], r["title"]))
        rows[city] = lst[:top]
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-calls", type=int, default=80)
    ap.add_argument("--cache-dir", default=str(PROJECT / "data" / "theme_scan_cache"))
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--out", default=str(PROJECT / "docs" / "anchor_discovery_swyhhs"))
    a = ap.parse_args(argv)
    from dotenv import load_dotenv
    load_dotenv(PROJECT / ".env")
    import collect_theme_pool as ctp
    import enrich_spots as es
    key = os.environ.get("TOURAPI_SERVICE_KEY") or os.environ.get("TOUR_API_KEY") or ""
    if not key:
        print("TourAPI 키가 없습니다(.env)")
        return 0
    budget = ctp.CachedBudget(es.Budget(a.max_calls), a.cache_dir)
    try:
        raw = collect(budget, key)
    except es.QuotaStop as e:
        print("중단:", e)
        return 0
    except Exception as e:
        print("수집 오류:", type(e).__name__)
        return 0
    rows = pick(raw, a.top)
    base = Path(a.out)
    base.parent.mkdir(parents=True, exist_ok=True)
    base.with_suffix(".json").write_text(json.dumps({"note": "내부 우선순위(점수)는 가이드 작성 순서용이며 화면에 노출하지 않는다.", "source": "TourAPI areaBasedList2(12·14·28)", "totals": {c: len(m) for c, m in raw.items()}, "rows": rows}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    lines = ["# 수원·용인·화성 앵커 후보 (TourAPI 시군구별 목록 기준)", "", "시별 전체 장소 수: " + ", ".join(f"{c} {len(m)}" for c, m in raw.items()) + f" — 아이 동반 점수 상위 {a.top}곳씩.", "",
             "> 내부 우선순위입니다(순위로 노출 금지). 운영시간·요금·주차는 가이드 작성 때 공식 출처로 채웁니다.", ""]
    for city, lst in rows.items():
        lines += [f"## {city}", "", "| # | 장소 | 주소 | 분류 | 사진 | 점수 |", "|---|---|---|---|---|---|"]
        for n, r in enumerate(lst, 1):
            lines.append(f"| {n} | {r['title']} | {r['address']} | {'문화시설' if r['contenttypeid'] == '14' else '레포츠' if r['contenttypeid'] == '28' else '관광지'} | {'O' if r['photo'] else '-'} | {r['score']} |")
        lines.append("")
    base.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"호출 {budget.used}회(캐시 {budget.hits}건) · " + ", ".join(f"{c} {len(m)}곳 중 후보 {len(rows[c])}" for c, m in raw.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
