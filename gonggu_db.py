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
"""


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


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(SCHEMA)


def upsert_gonggu(item: dict) -> None:
    """(influencer_name, product_name, start_date) 기준 중복 없이 upsert."""
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO gonggu
                (influencer_name, category, product_name, brand, start_date, end_date,
                 purchase_link, key_benefit, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(influencer_name, product_name, start_date) DO UPDATE SET
                category=excluded.category,
                brand=excluded.brand,
                end_date=excluded.end_date,
                purchase_link=excluded.purchase_link,
                key_benefit=excluded.key_benefit,
                updated_at=excluded.updated_at
            """,
            (
                item["influencer_name"], item["category"], item["product_name"],
                item.get("brand", ""), item["start_date"], item.get("end_date", ""),
                item.get("purchase_link", ""), item.get("key_benefit", ""),
                datetime.now(timezone.utc).isoformat(),
            ),
        )


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
