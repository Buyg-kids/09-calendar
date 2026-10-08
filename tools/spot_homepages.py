"""free_spots_seed.json 의 무료 나들이 시드 장소에 공식 홈페이지 주소(homepage_url)를 채우는 수동 실행 도구.

- 주소 후보는 아래 CANDIDATES 에 직접 관리한다(기억에 의존한 주소를 그대로 믿지 않는다).
- 실행하면 각 후보를 실제로 열어 보고(https, 리다이렉트 허용, 200 응답) 페이지 본문에 장소를 가리키는 키워드가 있을 때만 채택한다.
  접속 실패/인증서 오류/키워드 불일치 후보는 채우지 않는다 -> 상세 시트에서 기존 '공식·관련 안내 검색' 링크가 유지된다.
- 시드 전체(101곳)에 homepage_url 필드를 두고(없으면 ''), 기본은 드라이런, --apply 일 때만 파일을 쓴다.
- 다시 실행하면 이미 채워진 값도 재검증한다(죽은 링크 점검용).

  python tools/spot_homepages.py            # 검증만(파일 변경 없음)
  python tools/spot_homepages.py --apply    # 검증 통과분을 free_spots_seed.json 에 기록
"""
from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
SEED = ROOT / "free_spots_seed.json"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36 BUYG-link-check"

# spot id -> (공식 홈페이지 후보 URL, 본문에 하나라도 있어야 하는 키워드)
CANDIDATES: dict[str, tuple[str, list[str]]] = {
    "spot_01": ("https://www.museum.go.kr/site/child/home", ["어린이박물관", "국립중앙박물관"]),
    "spot_02": ("https://www.nfm.go.kr/", ["국립민속박물관"]),
    "spot_03": ("https://www.sisul.or.kr/open_content/childrenpark/", ["어린이대공원"]),
    "spot_04": ("https://seoulforest.or.kr/", ["서울숲"]),
    "spot_05": ("https://www.mmca.go.kr/", ["국립현대미술관"]),
    "spot_06": ("https://musenet.ggcf.kr/", ["경기도박물관", "경기문화재단"]),
    "spot_10": ("https://www.incheon.go.kr/park/index", ["인천대공원"]),
    "spot_12": ("https://www.world-scripts.go.kr/", ["세계문자박물관"]),
    "spot_13": ("https://chuncheon.museum.go.kr/", ["춘천박물관"]),
    "spot_16": ("https://cheongju.museum.go.kr/", ["청주박물관"]),
    "spot_19": ("https://i815.or.kr/", ["독립기념관"]),
    "spot_20": ("https://gongju.museum.go.kr/", ["공주박물관"]),
    "spot_21": ("https://buyeo.museum.go.kr/", ["부여박물관"]),
    "spot_22": ("https://www.science.go.kr/", ["국립중앙과학관"]),
    "spot_23": ("https://www.daejeon.go.kr/gar/index.do", ["한밭수목원"]),
    "spot_24": ("https://www.pa.go.kr/", ["대통령기록관"]),
    "spot_26": ("https://jeonju.museum.go.kr/", ["전주박물관"]),
    "spot_28": ("https://iksan.museum.go.kr/", ["익산박물관"]),
    "spot_29": ("https://gwangju.museum.go.kr/", ["광주박물관"]),
    "spot_31": ("https://naju.museum.go.kr/", ["나주박물관"]),
    "spot_32": ("https://gyeongju.museum.go.kr/", ["경주박물관"]),
    "spot_33": ("https://daegu.museum.go.kr/", ["대구박물관"]),
    "spot_35": ("https://www.knmm.or.kr/", ["해양박물관"]),
    "spot_38": ("https://gimhae.museum.go.kr/", ["김해박물관"]),
    "spot_40": ("https://jeju.museum.go.kr/", ["제주박물관"]),
    "spot_41": ("https://www.jeju.go.kr/hallasu/index.htm", ["한라수목원"]),
    "spot_43": ("https://www.hangeul.go.kr/", ["한글박물관"]),
    "spot_44": ("https://www.olympicpark.or.kr/", ["올림픽공원"]),
    "spot_45": ("https://parks.seoul.go.kr/dreamforest/", ["북서울꿈의숲", "꿈의숲"]),
    "spot_47": ("https://botanicpark.seoul.go.kr/", ["서울식물원"]),
    "spot_48": ("https://craftmuseum.seoul.kr/", ["서울공예박물관"]),
    "spot_49": ("https://museum.seoul.kr/", ["서울역사박물관"]),
    "spot_50": ("https://parks.seoul.go.kr/worldcuppark/", ["월드컵공원"]),
    "spot_51": ("https://parks.seoul.go.kr/seonyudo/", ["선유도"]),
    "spot_52": ("https://www.sisul.or.kr/open_content/cheonggye/", ["청계천"]),
    "spot_57": ("https://kna.forest.go.kr/", ["국립수목원"]),
    "spot_58": ("https://www.heyri.net/", ["헤이리"]),
    "spot_66": ("https://www.woljeongsa.org/", ["월정사"]),
    "spot_69": ("https://www.jikjiworld.com/", ["고인쇄박물관", "직지"]),
    "spot_70": ("https://hcs.cha.go.kr/", ["현충사"]),
    "spot_75": ("https://museum.bok.or.kr/", ["화폐박물관"]),
    "spot_76": ("https://www.kigam.re.kr/museum/", ["지질박물관"]),
    "spot_77": ("https://sejong.nl.go.kr/", ["세종도서관"]),
    "spot_78": ("https://hanok.jeonju.go.kr/", ["한옥마을"]),
    "spot_87": ("https://www.busan.go.kr/childpark/index", ["어린이대공원"]),
    "spot_93": ("https://whalemuseum.ulsan.go.kr/", ["고래박물관"]),
    "spot_100": ("https://www.jeju.go.kr/museum/index.htm", ["민속자연사박물관"]),
}


def check(sid: str, url: str, keywords: list[str]) -> tuple[str, bool, str]:
    """(spot id, 통과 여부, 사유)"""
    if not url.startswith("https://"):
        return sid, False, "https 아님"
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=15, allow_redirects=True)
    except requests.exceptions.SSLError:
        return sid, False, "인증서 오류"
    except requests.RequestException as e:
        return sid, False, "접속 실패(" + type(e).__name__ + ")"
    if r.status_code != 200:
        return sid, False, f"HTTP {r.status_code}"
    if not r.url.startswith("https://"):
        return sid, False, "https 로 이어지지 않음"
    if not r.encoding or r.encoding.lower() in ("iso-8859-1", "ascii"):
        r.encoding = r.apparent_encoding
    text = r.text
    hit = next((k for k in keywords if k in text), None)
    return (sid, True, f"키워드 '{hit}'") if hit else (sid, False, "본문에 장소 키워드 없음")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="검증 통과분을 시드 파일에 기록(기본은 검증만)")
    args = ap.parse_args()

    data = json.loads(SEED.read_text(encoding="utf-8"))
    spots = data["spots"]
    titles = {s["id"]: s["title"] for s in spots}
    with ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(lambda kv: check(kv[0], kv[1][0], kv[1][1]), CANDIDATES.items()))
    ok = {sid: CANDIDATES[sid][0] for sid, passed, _ in results if passed}
    for sid, passed, why in sorted(results):
        print(("OK   " if passed else "SKIP ") + f"{sid} {titles.get(sid, '?'):<18} {CANDIDATES[sid][0]}  - {why}")
    print(f"\n후보 {len(CANDIDATES)}곳 중 검증 통과 {len(ok)}곳 / 시드 {len(spots)}곳")
    if not args.apply:
        print("[검증만] 파일은 바꾸지 않았습니다. 기록하려면 --apply")
        return 0
    for s in spots:
        s["homepage_url"] = ok.get(s["id"], "")
    data["version"] = date.today().isoformat()
    SEED.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")   # 기존 파일과 같은 들여쓰기(diff 최소화)
    print(f"기록 완료: {SEED}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
