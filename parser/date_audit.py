"""누적 DB의 '월 전체(1일~말일)' 날짜를 전수 조사하고, 원문(캡션)이 남아 있는 건은 보정한다.

사용:
    python -m parser.date_audit            # 조사만 (DB 변경 없음)
    python -m parser.date_audit --apply    # 백업 후 보정 반영

DB에는 캡션 원문이 저장되지 않으므로, data/raw_collected.json(최근 게시물 5개)에 아직 남아 있는
게시물만 원문 대조가 가능하다. 원문이 없는 건은 임의 수정하지 않고 '확인필요'로만 보고한다.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
from datetime import date, datetime

from config import GONGGU_DB_PATH, RAW_COLLECTED_PATH
from parser.extract_schedule import _is_full_month, sanitize_month_span


def _captions_by_shortcode() -> dict[str, str]:
    caps: dict[str, str] = {}
    if RAW_COLLECTED_PATH.exists():
        for e in json.loads(RAW_COLLECTED_PATH.read_text(encoding="utf-8")):
            for cp in (e.get("instagram") or {}).get("captions", []):
                if cp.get("shortcode"):
                    caps[cp["shortcode"]] = cp.get("text", "")
    return caps


def audit(apply: bool = False) -> dict:
    conn = sqlite3.connect(GONGGU_DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute("SELECT * FROM gonggu")]
    caps = _captions_by_shortcode()
    result = {"total": len(rows), "full_month": [], "fixed": [], "unverifiable": [], "kept": []}

    for r in rows:
        try:
            s = date.fromisoformat(r["start_date"])
            e = date.fromisoformat(r["end_date"] or "")
        except ValueError:
            continue
        if not _is_full_month(s, e):
            continue
        result["full_month"].append(r)
        # 2026-09-22부터 DB에 캡션 원문을 직접 보관하므로 우선 그걸 쓰고, 그 전에
        # 저장된 오래된 행(caption_text 빈값)만 raw_collected.json(최근 5개 수집 창)으로 대체 대조한다.
        text = r.get("caption_text") or caps.get((r.get("post_url") or "").rstrip("/").split("/")[-1])
        if not text:
            result["unverifiable"].append(r)
            continue
        ref = datetime.fromisoformat(r["updated_at"][:10]).date()
        fixed = sanitize_month_span(dict(r), text, ref)
        if (fixed["start_date"], fixed["end_date"]) != (r["start_date"], r["end_date"]):
            result["fixed"].append((r, fixed))
        else:
            result["kept"].append(r)

    if apply and result["fixed"]:
        backup = GONGGU_DB_PATH.with_suffix(f".db.bak_{datetime.now():%Y%m%d_%H%M%S}")
        shutil.copy2(GONGGU_DB_PATH, backup)
        for old, new in result["fixed"]:
            conn.execute(
                "UPDATE gonggu SET start_date=?, end_date=? WHERE id=?",
                (new["start_date"], new["end_date"], old["id"]),
            )
        conn.commit()
        result["backup"] = str(backup)
    conn.close()
    return result


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    res = audit(ap.parse_args().apply)
    print(f"전체 {res['total']}건 중 월 전체(1일~말일) {len(res['full_month'])}건")
    print(f"  - 원문 대조 후 보정 {len(res['fixed'])}건 / 원문 근거 있어 유지 {len(res['kept'])}건 / 원문 미보관(확인필요) {len(res['unverifiable'])}건")
    for old, new in res["fixed"]:
        print(f"  [보정] #{old['id']} {old['influencer_name']} {old['product_name'][:30]}: "
              f"{old['start_date']}~{old['end_date']} -> {new['start_date']}~{new['end_date']}")
    for r in res["unverifiable"]:
        print(f"  [확인필요] #{r['id']} {r['influencer_name']} {r['product_name'][:30]} {r['start_date']}~{r['end_date']} post={r.get('post_url') or '-'}")
    if "backup" in res:
        print("백업:", res["backup"])
