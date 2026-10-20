"""경기 남부 앵커 장소(심층 가이드) 1차 후보 목록.  python tools/list_anchor_candidates.py [--top 50] [--out docs/anchor_candidates_gyeonggi_south]

기존 데이터(시드 free_spots_seed.json + 테마 풀 theme_spots_pool.json + 이미 만든 앵커 place_guides.json)에서 경기 남부(경기 중 북부 10개 시·군 제외) 장소를 모아 점수순으로 정리한다.
점수는 '가이드를 먼저 만들 가치'를 가늠하는 내부 우선순위일 뿐, 화면에 순위로 보이지 않는다(순위·추천 단정 금지 원칙).
 - 시드(공식 홈페이지·요금 일반 정보 있음) +3 / 사진 있음 +2 / 테마 풀에 걸린 테마 수(최대 +2) / 아이 관련 이름·분류 +2 / 홈페이지 있음 +1
 - 이미 앵커 가이드가 있는 곳은 제외. 시·군별 쏠림을 막으려고 시·군당 최대 --per-city(기본 6)곳, 수원·용인·화성(우선 거점)은 최대 --priority-per-city(기본 15)곳까지 올리고 점수 +3.
산출: <out>.json (기계용) + <out>.md (사람이 보는 표). 사실(운영시간·요금 등)은 여기 없다 - 가이드 작성 단계에서 공식 출처로 채운다."""
import argparse
import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NORTH = re.compile(r"(고양|구리|남양주|동두천|양주|의정부|파주|포천|가평|연천)")
PRIORITY = ("수원시", "용인시", "화성시")   # 사용자 결정(10/20): 검색량이 큰 '수원 아기랑·용인 아이랑·화성 아기랑' 3대 거점을 먼저 채운다
KID = re.compile("어린이|아이|키즈|가족|체험|동물|공룡|과학|박물관|미술관|생태|수목원|공원|농장|목장|숲")


def south(addr):
    two = " ".join((addr or "").split(" ")[:2])
    return (addr or "").startswith("경기") and not NORTH.search(two)


def city(addr):
    p = (addr or "").split(" ")
    return p[1] if len(p) > 1 else ""


def load(path, default):
    try:
        return json.loads((ROOT / path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def norm(s):
    return re.sub(r"\s+", "", re.sub(r"\(.*?\)", "", s or ""))


def collect():
    pool = load("theme_spots_pool.json", {}).get("themes", {})
    seeds = load("free_spots_seed.json", {}).get("spots", [])
    enr = load("spot_enriched.json", {}).get("spots", {})
    guides = load("place_guides.json", {}).get("places", {})
    have = {norm(g.get("name", "")) for g in guides.values()} | set(guides)
    cand = {}
    for s in seeds:
        if s.get("region") == "경기" and south(s.get("address", "")) and s["id"] not in guides and norm(s["title"]) not in have:
            e = enr.get(s["id"]) or {}
            cand["seed:" + s["id"]] = {"key": "seed:" + s["id"], "title": s["title"], "address": s.get("address", ""), "city": city(s.get("address", "")), "source": "seed", "themes": [],
                                       "photo": bool(e.get("photo")), "category": s.get("category", ""), "homepage": bool(s.get("homepage_url")), "free": "무료" in (s.get("tag") or "")}
    for tid, v in pool.items():
        for i in v.get("items", []):
            if i.get("region") != "경기" or not south(i.get("address", "")) or norm(i["title"]) in have:
                continue
            if any(norm(c["title"]) == norm(i["title"]) for c in cand.values()):
                for c in cand.values():
                    if norm(c["title"]) == norm(i["title"]) and tid not in c["themes"]:
                        c["themes"].append(tid)
                continue
            o = cand.setdefault("pool:" + i["id"], {"key": "pool:" + i["id"], "title": i["title"], "address": i["address"], "city": city(i["address"]), "source": "theme_pool", "themes": [],
                                                    "photo": bool(i.get("photo")), "category": i.get("category", ""), "homepage": False, "free": False})
            if tid not in o["themes"]:
                o["themes"].append(tid)
    return list(cand.values())


def score(o):
    return (3 if o["city"] in PRIORITY else 0) + (3 if o["source"] == "seed" else 0) + (2 if o["photo"] else 0) + min(len(o["themes"]), 2) + (2 if KID.search(o["title"] + o["category"]) else 0) + (1 if o["homepage"] else 0)


def pick(cands, top, per_city, priority_per_city=15):
    out, n = [], Counter()
    for o in sorted(cands, key=lambda o: (-score(o), o["title"])):
        if n[o["city"]] >= (priority_per_city if o["city"] in PRIORITY else per_city):
            continue
        n[o["city"]] += 1
        out.append(dict(o, score=score(o)))
        if len(out) >= top:
            break
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=50)
    ap.add_argument("--per-city", type=int, default=6)
    ap.add_argument("--priority-per-city", type=int, default=15)
    ap.add_argument("--out", default=str(ROOT / "docs" / "anchor_candidates_gyeonggi_south"))
    a = ap.parse_args()
    cands = collect()
    rows = pick(cands, a.top, a.per_city, a.priority_per_city)
    base = Path(a.out)
    base.parent.mkdir(parents=True, exist_ok=True)
    base.with_suffix(".json").write_text(json.dumps({"note": "내부 우선순위(점수)는 가이드 작성 순서용이며 화면에 노출하지 않는다.", "total_candidates": len(cands), "rows": rows}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    cities = Counter(r["city"] for r in rows)
    lines = ["# 경기 남부 앵커 장소 1차 후보 (기존 데이터 기준)", "",
             f"후보 {len(cands)}곳 중 점수순 {len(rows)}곳(시·군당 최대 {a.per_city}곳, 우선 거점 {', '.join(PRIORITY)}은 최대 {a.priority_per_city}곳). 시·군별: " + ", ".join(f"{c} {n}" for c, n in cities.most_common()), "",
             "> 내부 우선순위입니다(순위로 노출 금지). 운영시간·요금·주차는 가이드 작성 때 공식 출처로 채웁니다.", "",
             "| # | 장소 | 시·군 | 출처 | 테마 | 사진 | 점수 |", "|---|---|---|---|---|---|---|"]
    for n, r in enumerate(rows, 1):
        lines.append(f"| {n} | {r['title']} | {r['city']} | {'시드' if r['source'] == 'seed' else '테마 풀'} | {', '.join(r['themes']) or '-'} | {'O' if r['photo'] else '-'} | {r['score']} |")
    base.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"후보 {len(cands)}곳 -> {len(rows)}곳 / 시·군 {dict(cities)}")


if __name__ == "__main__":
    main()
