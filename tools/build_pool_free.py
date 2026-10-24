"""입장료가 무료로 확인된 테마 풀 장소만 담은 작은 파일 pool_free.json 을 만든다.  python tools/build_pool_free.py [--check]

theme_spots_pool.json(테마 풀) 중 pool_enriched.json 의 세부정보에서 입장료 기준 무료(details.free == true)로 판정된 장소만 골라, 테마 풀과 같은 모양({themes:{테마id:{items}}})으로 저장한다.
홈은 이 작은 파일을 처음부터 불러와 '전국 무료 나들이'에 합친다(테마 풀 전체 400KB+ 는 테마 칩을 누를 때만 받는다). 장소가 속한 테마는 그대로 유지하므로 테마 칩 결과에서도 사라지지 않는다.
 - 지어내지 않는다: 무료 판정은 collect_pool_enriched 가 입장료 필드만 보고 한 결과다(주차요금 '무료'는 입장 무료가 아님). 네트워크 호출 없음.
 - --check: 파일을 쓰지 않고 현재 pool_free.json 이 최신 풀·세부정보와 일치하는지만 검사(어긋나면 종료코드 1).
산출: pool_free.json (공개, schema_version 1). 항목에는 화면이 쓰는 필드만 남긴다."""
import argparse
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KEEP = ("id", "title", "address", "region", "geo", "category", "photo", "provenance", "contentid")


def build(pool: dict, enriched: dict) -> dict:
    free_ids = {pid for pid, rec in (enriched.get("spots") or {}).items() if (rec.get("details") or {}).get("free") is True}
    themes = {}
    for tid, th in (pool.get("themes") or {}).items():
        items = [dict({k: it[k] for k in KEEP if k in it}, free=True) for it in th.get("items") or [] if it.get("id") in free_ids]
        if items:
            themes[tid] = {"label": th.get("label", tid), "items": items}
    return {"schema_version": 1, "generated_at": enriched.get("generated_at") or date.today().isoformat(), "themes": themes}


def unique_ids(doc: dict) -> set:
    return {it["id"] for th in (doc.get("themes") or {}).values() for it in th.get("items") or []}


def errors(doc: dict, enriched: dict) -> list:
    errs = []
    if doc.get("schema_version") != 1:
        errs.append("schema_version 은 1 이어야 함")
    spots = enriched.get("spots") or {}
    for tid, th in (doc.get("themes") or {}).items():
        for it in th.get("items") or []:
            if it.get("free") is not True:
                errs.append(f"{tid}/{it.get('id')}: free 가 true 가 아님")
            if not (spots.get(it.get("id"), {}).get("details") or {}).get("free") is True:
                errs.append(f"{tid}/{it.get('id')}: pool_enriched 에서 무료로 확인된 장소가 아님")
            if not isinstance(it.get("geo"), dict):
                errs.append(f"{tid}/{it.get('id')}: 좌표 없음")
    return errs


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool", default=str(ROOT / "theme_spots_pool.json"))
    ap.add_argument("--enriched", default=str(ROOT / "pool_enriched.json"))
    ap.add_argument("--out", default=str(ROOT / "pool_free.json"))
    ap.add_argument("--check", action="store_true", help="파일을 쓰지 않고 최신 여부만 검사")
    a = ap.parse_args(argv)
    pool = json.loads(Path(a.pool).read_text(encoding="utf-8"))
    enriched = json.loads(Path(a.enriched).read_text(encoding="utf-8"))
    doc = build(pool, enriched)
    outp = Path(a.out)
    if a.check:
        cur = json.loads(outp.read_text(encoding="utf-8")) if outp.exists() else None
        if cur is None:
            print("pool_free.json 없음")
            return 1
        errs = errors(cur, enriched)
        if unique_ids(cur) != unique_ids(doc):
            errs.append("무료 장소 목록이 최신 pool_enriched.json 과 달라요(build_pool_free.py 를 다시 실행)")
        for e in errs:
            print("-", e)
        print(f"{len(unique_ids(cur))}곳 검사, 오류 {len(errs)}건")
        return 1 if errs else 0
    outp.write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"무료 확인 {len(unique_ids(doc))}곳(테마 {len(doc['themes'])}개) -> {outp}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
