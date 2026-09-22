"""gonggu.db 공유 데이터 계층.

parser/extract_schedule.py(Agent 2)가 쓰고, generator/·web/이 읽는다.
DB 스키마나 저장 위치를 바꿔야 할 때 이 파일 하나만 고치면 된다
(다른 모듈은 전부 이 파일의 함수만 통해 접근한다).
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator, Optional

from config import GONGGU_DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS gonggu (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    influencer_name TEXT NOT NULL,
    category TEXT NOT NULL,
    product_name TEXT NOT NULL,
    brand TEXT,
    start_date TEXT NOT NULL,
    end_date TEXT,
    purchase_link TEXT,
    key_benefit TEXT,
    updated_at TEXT NOT NULL,
    UNIQUE(influencer_name, product_name, start_date)
);

CREATE INDEX IF NOT EXISTS idx_gonggu_dates ON gonggu(start_date, end_date);

-- 같은 게시물이 "최신 5개"에 며칠씩 계속 걸려있는 경우가 많아, 매일 밤 이미 한 번
-- 검사한(성공/무결과 모두 포함) 캡션/멀티링크 텍스트를 그대로 다시 Claude에 보내던
-- 중복 호출을 막기 위한 캐시. key는 원문 텍스트의 해시 - 텍스트가 실제로 바뀌면
-- (예: 캡션 수정) 다른 해시가 되어 자연스럽게 다시 검사된다.
CREATE TABLE IF NOT EXISTS processed_blobs (
    text_hash TEXT PRIMARY KEY,
    checked_at TEXT NOT NULL
);
"""

# Buyg 리브랜딩(위시버니 스타일 UI)에서 상품 이미지/가격을 노출하기 위해 나중에
# 추가된 컬럼들. 기존 DB 파일에는 없을 수 있으므로 init_db()에서 마이그레이션한다
# (CREATE TABLE IF NOT EXISTS는 이미 존재하는 테이블의 컬럼을 추가해주지 않음).
_MIGRATION_COLUMNS = {
    "image_url": "TEXT",  # 인스타 게시물 대표 이미지 URL (없으면 프런트에서 카테고리별 플레이스홀더로 대체)
    "price": "TEXT",      # 본문에 가격이 명시 안 된 경우가 많아 숫자가 아닌 TEXT (예: '가격공개예정', '19,900원')
    "post_url": "TEXT",   # 이 공구 정보를 뽑아낸 인스타그램 게시물/릴스 원본 URL. purchase_link가
                           # 없을 때(예: "댓글 달면 자동DM" 공구) 사용자를 이 게시물로 보내 댓글을 달 수 있게 함
    "caption_text": "TEXT",  # 2026-09-22 추가: 이 항목을 뽑아낸 원본 캡션/멀티링크 텍스트 그대로 보관.
                              # 게시물이 "최신 5개" 수집 창에서 밀려나면 raw_collected.json에서도 사라져
                              # 날짜/가격 파싱 오류를 원문과 대조해 검증할 방법이 없었던 문제(2026-09-21
                              # 월 전체 날짜 오류 조사) 때문에 도입 - 트러블슈팅 전용, 프런트에 노출 안 함.
}


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    GONGGU_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(GONGGU_DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _migrate(conn: sqlite3.Connection) -> None:
    existing = {row[1] for row in conn.execute("PRAGMA table_info(gonggu)").fetchall()}
    for column, col_type in _MIGRATION_COLUMNS.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE gonggu ADD COLUMN {column} {col_type}")


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)


def upsert_gonggu(item: dict) -> None:
    """(influencer_name, product_name, start_date) 기준 중복 없이 upsert."""
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO gonggu
                (influencer_name, category, product_name, brand, start_date, end_date,
                 purchase_link, key_benefit, image_url, price, post_url, caption_text, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(influencer_name, product_name, start_date) DO UPDATE SET
                category=excluded.category,
                brand=excluded.brand,
                end_date=excluded.end_date,
                purchase_link=excluded.purchase_link,
                key_benefit=excluded.key_benefit,
                image_url=CASE WHEN excluded.image_url != '' THEN excluded.image_url ELSE gonggu.image_url END,
                price=excluded.price,
                post_url=CASE WHEN excluded.post_url != '' THEN excluded.post_url ELSE gonggu.post_url END,
                caption_text=CASE WHEN excluded.caption_text != '' THEN excluded.caption_text ELSE gonggu.caption_text END,
                updated_at=excluded.updated_at
            """,
            (
                item["influencer_name"], item["category"], item["product_name"],
                item.get("brand", ""), item["start_date"], item.get("end_date", ""),
                item.get("purchase_link", ""), item.get("key_benefit", ""),
                item.get("image_url", ""), item.get("price", ""), item.get("post_url", ""),
                item.get("caption_text", ""),
                datetime.now(timezone.utc).isoformat(),
            ),
        )


def is_blob_processed(text_hash: str) -> bool:
    """이 텍스트(해시)를 이전 실행에서 이미 확정적으로 검사했으면 True.
    확정적 = Claude API 오류(크레딧 소진 등)로 못 본 게 아니라, 실제로 결과를
    받은 경우만 - parser/extract_schedule.py의 run()이 그 경우에만 mark를 호출."""
    with get_conn() as conn:
        row = conn.execute("SELECT 1 FROM processed_blobs WHERE text_hash = ?", (text_hash,)).fetchone()
        return row is not None


def mark_blob_processed(text_hash: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO processed_blobs (text_hash, checked_at) VALUES (?, ?)",
            (text_hash, datetime.now(timezone.utc).isoformat()),
        )


def list_missing_images() -> list[dict[str, Any]]:
    """image_url이 비어 있는 행 조회 (parser/image_fallback.py가 사용)."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, product_name, brand, category FROM gonggu WHERE image_url IS NULL OR image_url = ''"
        ).fetchall()
        return [dict(r) for r in rows]


def update_image_url(row_id: int, image_url: str) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE gonggu SET image_url = ? WHERE id = ?", (image_url, row_id))


def list_gonggu(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    category: Optional[str] = None,
) -> list[dict[str, Any]]:
    """날짜 범위/카테고리로 필터링해 공구 목록 조회 (generator/web이 사용)."""
    query = "SELECT * FROM gonggu WHERE 1=1"
    params: list[Any] = []
    if start_date:
        query += " AND (end_date IS NULL OR end_date = '' OR end_date >= ?)"
        params.append(start_date)
    if end_date:
        query += " AND start_date <= ?"
        params.append(end_date)
    if category:
        query += " AND category = ?"
        params.append(category)
    query += " ORDER BY start_date"
    with get_conn() as conn:
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


if __name__ == "__main__":
    init_db()
    print(f"gonggu.db 초기화 완료: {GONGGU_DB_PATH}")
